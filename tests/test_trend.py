import math
import os
import time

import pytest

from bot import config, signals
from bot.main import Bot

H4 = 4 * 3600


def make_bars(n=4800, seed=1):
    """Synthetic 4h candles: up-trend, crash, then up-trend again."""
    import random
    rnd = random.Random(seed)
    now = (int(time.time()) // H4) * H4 - H4          # last one already closed
    t0 = now - (n - 1) * H4
    px, bars = 30000.0, []
    for i in range(n):
        drift = 0.002 if (i // 800) % 2 == 0 else -0.0015
        o = px
        px = px * (1 + drift + rnd.gauss(0, 0.01))
        h, l = max(o, px) * 1.003, min(o, px) * 0.997
        bars.append((t0 + i * H4, o, h, l, px))
    return bars


class TrendClient:
    def __init__(self, bars):
        self.bars = {"BTCUSD": bars, "ETHUSD": [(t, o / 15, h / 15, l / 15, c / 15) for t, o, h, l, c in bars]}
        self.mark = {s: b[-1][4] for s, b in self.bars.items()}
        self.orders = []

    def products(self, contract_types=None, **k):
        return [{"id": 1, "symbol": "BTCUSD", "contract_value": "0.001"},
                {"id": 2, "symbol": "ETHUSD", "contract_value": "0.01"}]

    def ticker(self, symbol):
        m = self.mark[symbol]
        return {"mark_price": m, "funding_rate": 0.0,
                "quotes": {"best_bid": m * 0.9999, "best_ask": m * 1.0001}}

    def candles(self, symbol, resolution, start, end):
        return [{"time": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4]}
                for b in self.bars[symbol] if start <= b[0] < end]

    def positions(self):
        return []

    def place_order(self, *a, **k):
        self.orders.append((a, k))
        return {"id": len(self.orders)}

    def cancel_all(self):
        pass


@pytest.fixture
def tsettings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith(("DELTA_", "LIVE_", "DRY_", "STRATEGY", "TREND_")):
            monkeypatch.delenv(k)
    monkeypatch.setenv("STRATEGY", "trend_portfolio")
    monkeypatch.setenv("DRY_RUN_EQUITY_USD", "10000")
    return config.load()


def make(tsettings, bars):
    b = Bot(tsettings)
    cl = TrendClient(bars)
    b._wire(cl)
    return b, cl


# ---------- signals ----------
def test_ema_and_atr_basic():
    assert signals.ema([1, 1, 1], 5) == [1, 1, 1]
    bars = [(0, 10, 11, 9, 10)] * 50
    assert abs(signals.atr(bars)[-1] - 2) < 1e-9


def test_trend_signal_follows_direction():
    bars = make_bars()
    sig = signals.trend_raw(bars, 20, 100)
    assert sig[790] == 1          # deep in an up-leg
    assert sig[1590] == 0         # deep in a down-leg


def test_breakout_needs_new_high_then_exits_on_cross():
    bars = make_bars()
    sig = signals.breakout_raw(bars, 20, 30, 150)
    assert sig[:150] == [0] * 150
    assert sig[790] == 1 and sig[1590] == 0


def test_simulate_stop_blocks_until_reset():
    # rise, then a gap down through the stop, while trend signal stays 1
    bars = [(i, 100 + i, 101 + i, 99 + i, 100 + i) for i in range(400)]
    bars.append((400, 499, 499, 300, 310))
    bars += [(401 + i, 310, 311, 309, 310) for i in range(3)]
    sig = [1] * len(bars)
    rets, trades, st = signals.simulate(bars, sig)
    assert trades == 1 and st["pos"] == 0 and st["blocked"]


def test_tune_returns_grid_member():
    bars = make_bars(3000)
    assert signals.tune(bars, "trend") in signals.TREND_GRID
    assert signals.tune(bars, "breakout") in signals.BRK_GRID


# ---------- live portfolio (dry-run) ----------
def test_portfolio_opens_1x_split_with_atr_stop(tsettings):
    bars = make_bars(4800)[:3990]            # ends inside an up-leg
    # shift times so the last bar is the newest closed candle
    shift = ((int(time.time()) // H4) * H4 - H4) - bars[-1][0]
    bars = [(t + shift, o, h, l, c) for t, o, h, l, c in bars]
    b, cl = make(tsettings, bars)
    tp = b.trend
    for sym in tp.coins:
        tp.coin_state(sym)["blocked"] = False
    assert b.tick() == "ok"
    opened = {x["name"]: x for x in b.j.open_baskets()}
    btc = [x for n, x in opened.items() if n.startswith("trend:BTCUSD")]
    assert btc, opened
    leg = btc[0]["legs"][0]
    assert leg["side"] == "buy"
    assert leg["notional"] <= 10000 * 0.5 * 1.0 + 1          # 1x, half the money
    assert leg["stop"] < leg["price"]
    assert abs((leg["price"] - leg["stop"]) - 3 * tp.state["BTCUSD"]["target"]["atr"]) < 1
    assert cl.orders == []                                    # dry run: nothing sent
    # second tick does not open again
    b.tick()
    assert len([x for x in b.j.open_baskets() if x["name"].startswith("trend:BTCUSD")]) == 1


def test_portfolio_exits_when_signal_off_and_stop_blocks(tsettings):
    bars = make_bars(4800)[:3990]
    shift = ((int(time.time()) // H4) * H4 - H4) - bars[-1][0]
    bars = [(t + shift, o, h, l, c) for t, o, h, l, c in bars]
    b, cl = make(tsettings, bars)
    tp = b.trend
    for sym in tp.coins:
        tp.coin_state(sym)["blocked"] = False
    b.tick()
    pos = tp.position("BTCUSD")
    assert pos
    # price crashes through the stop -> closed + blocked
    cl.mark["BTCUSD"] = pos["legs"][0]["stop"] - 10
    b.tick()
    assert tp.position("BTCUSD") is None
    assert tp.state["BTCUSD"]["blocked"]
    # still blocked next tick even though signal is 1
    cl.mark["BTCUSD"] = bars[-1][4]
    b.tick()
    assert tp.position("BTCUSD") is None
    # signal turns off -> unblocked
    tp.state["BTCUSD"]["target"]["want"] = 0
    tp.state["BTCUSD"]["blocked"] = False
    tp.state["BTCUSD"]["target"]["want"] = 1
    b.tick()
    pos = tp.position("BTCUSD")
    assert pos
    tp.state["BTCUSD"]["target"]["want"] = 0
    b.tick()
    assert tp.position("BTCUSD") is None


def test_kill_switch_stops_new_trend_entries(tsettings):
    bars = make_bars(4800)[:3990]
    shift = ((int(time.time()) // H4) * H4 - H4) - bars[-1][0]
    bars = [(t + shift, o, h, l, c) for t, o, h, l, c in bars]
    b, cl = make(tsettings, bars)
    open(tsettings.kill_file, "w").write("x")
    assert b.tick() == "killed"
    assert b.j.open_baskets() == []


def test_summary_runs(tsettings):
    bars = make_bars(4800)
    b, cl = make(tsettings, bars)
    out = b.trend.summary()
    assert "BTCUSD" in out and "ETHUSD" in out


def test_trading_capital_caps_live_equity(tsettings, monkeypatch):
    tsettings.dry_run = False
    tsettings.trading_capital_usd = 1150
    b, cl = make(tsettings, make_bars(700))
    cl.wallet = lambda: [{"asset_symbol": "USD", "balance": "50000", "available_balance": "50000"}]
    assert b.equity() == 1150
    tsettings.trading_capital_usd = 0
    assert b.equity() == 50000
