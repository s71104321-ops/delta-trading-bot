"""Live trend portfolio: BTC EMA trend + ETH breakout, long only, 1x, equal money per coin.

Every new closed 4h candle: refresh signals (re-tune settings if older than 182 days).
Every loop (30s): act on the latest signal — open with a limit order and a 3xATR stop placed on
Delta, close when the signal turns off, and notice if Delta's stop already closed the position.
"""
import json
import logging
import math
import os
import time

from . import signals
from .config import BASE_URLS
from .delta_client import DeltaClient
from .futures import FuturesError, FuturesTrader

log = logging.getLogger("trend")
H4 = 4 * 3600
RETUNE_DAYS = 182


class TrendPortfolio:
    def __init__(self, bot, state_path="data/trend_state.json"):
        self.bot, self.s = bot, bot.s
        self.coins = dict(x.split(":") for x in self.s.trend_coins.split(","))
        self.traders = {sym: FuturesTrader(bot.c, bot.j, bot.n, self.s, dry_run=self.s.dry_run, symbol=sym)
                        for sym in self.coins}
        # signals always use REAL market candles (public data, no key), even when trading on testnet
        self.data = DeltaClient(BASE_URLS["live"])
        self.path = state_path
        self.state = self._load()

    # ---------- state ----------
    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.path)

    def coin_state(self, sym):
        return self.state.setdefault(sym, {"params": None, "tuned_at": 0, "last_candle": 0,
                                           "blocked": None, "target": None})

    # ---------- data ----------
    def candles(self, sym, days=760):
        now = int(time.time())
        start = now - days * 86400
        rows, t = {}, start
        while t < now:
            t2 = min(t + 1900 * H4, now)
            for c in self.data.candles(sym, "4h", t, t2) or []:
                rows[int(c["time"])] = (int(c["time"]), float(c["open"]), float(c["high"]),
                                        float(c["low"]), float(c["close"]))
            t = t2
        bars = sorted(rows.values())
        return [b for b in bars if b[0] + H4 <= now]     # closed candles only

    # ---------- helpers ----------
    def position(self, sym):
        for b in self.bot.j.open_baskets():
            if b["name"].startswith(f"trend:{sym}"):
                return b
        return None

    def exchange_size(self, product_id):
        for p in self.bot.c.positions() or []:
            if int(p.get("product_id", -1)) == int(product_id):
                return int(p.get("size", 0))
        return 0

    # ---------- main ----------
    def refresh(self, sym, force=False):
        st = self.coin_state(sym)
        now = int(time.time())
        newest_closed = (now // H4) * H4 - H4
        if not force and st["last_candle"] >= newest_closed and st["target"]:
            return st["target"]
        bars = self.candles(sym)
        if len(bars) < 600:
            raise FuturesError(f"not enough candles for {sym}: {len(bars)}")
        kind = self.coins[sym]
        if not st["params"] or now - st["tuned_at"] > RETUNE_DAYS * 86400:
            st["params"] = list(signals.tune(bars[-730 * 6:], kind))
            st["tuned_at"] = now
            self.bot.n.send(f"🔧 {sym} re-tuned: settings {st['params']} ({kind})")
        t = signals.target(bars, kind, st["params"])
        if st["blocked"] is None:                       # first run: start in the backtest's state
            st["blocked"] = t["blocked"]
        if t["want"] == 0:
            st["blocked"] = False                       # signal reset -> allowed to re-enter later
        st["target"] = t
        st["last_candle"] = bars[-1][0]
        self._save()
        log.info("%s target %s", sym, t)
        return t

    def tick(self, equity):
        for sym, kind in self.coins.items():
            try:
                self._tick_coin(sym, equity)
            except Exception as e:  # noqa: BLE001
                log.exception("trend %s", sym)
                self.bot.j.event("trend_error", symbol=sym, error=str(e))

    def _tick_coin(self, sym, equity):
        st = self.coin_state(sym)
        t = self.refresh(sym)
        ft = self.traders[sym]
        pos = self.position(sym)
        if pos and not self.s.dry_run and self.exchange_size(pos["legs"][0]["product_id"]) == 0:
            # Delta's stop-loss already closed it
            leg = pos["legs"][0]
            exit_px = leg["stop"]
            pnl = (exit_px - leg["price"]) * leg["size"] * leg["contract_value"]
            self.bot.j.close_basket(pos["id"], exit_px * leg["size"] * leg["contract_value"], pnl, "stopped")
            st["blocked"] = True
            self._save()
            self.bot.n.send(f"🛑 {sym} stop-loss hit on Delta at ~{exit_px}. P&L ~{pnl:+.2f} USD. "
                            "Waiting for a fresh signal.")
            return
        if pos and self.s.dry_run:                      # simulate the exchange stop in dry-run
            mark = ft.quote()["mark"]
            if mark and mark <= pos["legs"][0]["stop"]:
                self.bot.exe.close(pos, "stop_loss")
                st["blocked"] = True
                self._save()
                return
        if pos and t["want"] == 0:
            ft.cancel_stop(pos)
            pnl = self.bot.exe.close(pos, "trend_exit")
            self.bot.n.send(f"📉 {sym} trend over — closed. P&L {pnl:+.2f} USD")
            return
        if not pos and t["want"] == 1 and not st["blocked"]:
            if self.bot.risk.kill_switch_on():
                return
            q = ft.quote()
            price = q["ask"]
            cv = float(ft.product().get("contract_value", 0.001))
            weight = 1.0 / len(self.coins)
            size = math.floor(equity * weight * self.s.trend_leverage / (price * cv))
            if size < 1:
                self.bot.j.event("trend_skip", symbol=sym, reason="equity too small", equity=equity)
                return
            tick = float(ft.product().get("tick_size") or 0.1)
            stop = round(math.floor((price - 3 * t["atr"]) / tick) * tick, 6)
            leg = {"symbol": ft.product()["symbol"], "product_id": ft.product()["id"], "type": "perp",
                   "side": "buy", "size": size, "price": round(price, 1), "stop": stop,
                   "contract_value": cv, "bid": q["bid"], "ask": q["ask"],
                   "notional": size * price * cv, "risk_usd": size * (price - stop) * cv}
            ft.open(leg, name=f"trend:{sym}")

    def summary(self):
        lines = []
        for sym, kind in self.coins.items():
            t = self.refresh(sym, force=True)
            st = self.coin_state(sym)
            pos = self.position(sym)
            lines.append(f"{sym} ({kind}, settings {st['params']}): "
                         f"{'IN TRADE' if pos else ('WANTS TO BUY' if t['want'] and not st['blocked'] else 'WAITING')}"
                         f" | close {t['close']:.1f} ATR {t['atr']:.1f}{' | blocked after stop' if st['blocked'] else ''}")
        return "\n".join(lines)
