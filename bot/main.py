"""Bot entry point.

  python -m bot.main run                     # main loop (systemd runs this)
  python -m bot.main status                  # wallet, positions, open baskets
  python -m bot.main open-test bull_call_spread   # open one test basket (testnet / dry-run)
  python -m bot.main open-test-futures long 1.0   # test perp trade, 1% stop (testnet / dry-run)
  python -m bot.main kill                    # cancel orders + close all baskets now
"""
import logging
import os
import sys
import time

from . import config, strategies
from .basket import BasketError, BasketExecutor, OptionChain, net_premium
from .delta_client import DeltaClient
from .futures import FuturesError, FuturesTrader
from .journal import Journal
from .notifier import Notifier
from .risk import RiskBlocked, RiskManager

log = logging.getLogger("bot")


class Bot:
    def __init__(self, s):
        self.s = s
        self.c = DeltaClient(s.base_url, s.api_key, s.api_secret)
        self.j = Journal(s.journal_path)
        self.n = Notifier(s.telegram_token, s.telegram_chat_id)
        self.risk = RiskManager(s, self.j)
        self.exe = BasketExecutor(self.c, self.j, self.n, dry_run=s.dry_run)
        self.chain = OptionChain(self.c, s.underlying)
        self.fut = FuturesTrader(self.c, self.j, self.n, s, dry_run=s.dry_run)
        self.strategy = strategies.get(os.getenv("STRATEGY", "none"))

    def _wire(self, client):
        """Point every component at one client (used by tests)."""
        self.c = self.exe.c = self.chain.c = self.fut.c = client

    def equity(self):
        if self.s.dry_run:
            return self.s.dry_run_equity_usd
        bals = self.c.wallet()
        usd = [b for b in bals if b.get("asset_symbol") in ("USD", "USDT")]
        return float(usd[0].get("available_balance", 0)) if usd else 0.0

    def try_open_futures(self, side, stop_pct):
        leg = self.fut.plan(side, stop_pct, self.equity())
        self.risk.check_new_futures(leg, self.unrealized())
        self.j.event("signal", futures=side, stop_pct=stop_pct, size=leg["size"])
        return self.fut.open(leg)

    def spot(self):
        t = self.c.ticker(f"{self.s.underlying}USD")
        return float(t.get("spot_price") or t.get("mark_price"))

    def try_open(self, template):
        exp, chain = self.chain.load()
        legs = self.chain.resolve(strategies.TEMPLATES[template], self.spot(), chain)
        prem = net_premium(legs)
        self.risk.check_new_basket(legs, prem, self.unrealized())
        self.j.event("signal", template=template, expiry=str(exp), premium=prem)
        return self.exe.open(f"{template}@{exp:%d%b}", legs)

    def unrealized(self):
        return sum(self.exe.mark_pnl(b) for b in self.j.open_baskets())

    def flatten(self, reason):
        if not self.s.dry_run:
            try:
                self.c.cancel_all()
            except Exception as e:  # noqa: BLE001
                self.n.send(f"🚨 cancel_all failed: {e}")
        for b in self.j.open_baskets():
            try:
                self.exe.close(b, reason)
            except Exception as e:  # noqa: BLE001
                self.n.send(f"🚨 close failed for {b['id']}: {e} — check Delta manually!")

    def tick(self):
        if self.risk.kill_switch_on():
            if self.j.open_baskets():
                self.flatten("kill_switch")
            return "killed"
        # exits first
        for b in self.j.open_baskets():
            if FuturesTrader.is_perp(b):
                if FuturesTrader.stop_hit(b, self.fut.quote()["mark"]):
                    self.fut.cancel_stop(b)
                    self.exe.close(b, "futures_stop_loss")
                continue
            pnl = self.exe.mark_pnl(b)
            if self.risk.basket_should_exit(pnl, b["entry_cost"]):
                self.exe.close(b, "basket_stop_loss")
        if self.risk.daily_pnl(self.unrealized()) <= -self.s.max_daily_loss_usd:
            self.flatten("daily_loss_cap")
            open(self.s.kill_file, "w").write("daily loss cap hit\n")
            self.n.send("🛑 Daily loss cap hit. Kill switch ON. Delete the KILL file to resume.")
            return "daily_cap"
        # entries
        sig = self.strategy.entry_signal({"spot": self.spot()})
        if sig:
            try:
                if isinstance(sig, dict) and sig.get("kind") == "futures":
                    self.try_open_futures(sig["side"], sig["stop_pct"])
                else:
                    self.try_open(sig if isinstance(sig, str) else sig["template"])
            except (RiskBlocked, BasketError, FuturesError) as e:
                self.j.event("blocked", signal=str(sig), reason=str(e))
        return "ok"

    def run(self):
        mode = f"{self.s.env.upper()}{' DRY-RUN' if self.s.dry_run else ''}"
        self.n.send(f"🤖 Bot started [{mode}] strategy={self.strategy.name}")
        errors = 0
        while True:
            try:
                self.tick()
                errors = 0
            except Exception as e:  # noqa: BLE001
                errors += 1
                log.exception("tick failed")
                if errors in (3, 10) or errors % 50 == 0:
                    self.n.send(f"⚠️ {errors} errors in a row: {e}")
            time.sleep(self.s.poll_seconds)


def main(argv=None):
    argv = argv or sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = config.load()
    bot = Bot(s)
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        bot.run()
    elif cmd == "status":
        print("mode:", s.env, "dry_run:", s.dry_run)
        if s.api_key:
            print("wallet:", bot.c.wallet())
            print("positions:", bot.c.positions())
        for b in bot.j.open_baskets():
            print("basket", b["id"], b["name"], "P&L", round(bot.exe.mark_pnl(b), 2))
    elif cmd == "open-test":
        if s.env == "live":
            raise SystemExit("open-test is for testnet only")
        print("opened", bot.try_open(argv[1] if len(argv) > 1 else "bull_call_spread"))
    elif cmd == "open-test-futures":
        if s.env == "live":
            raise SystemExit("open-test-futures is for testnet only")
        side = {"long": "buy", "short": "sell"}[argv[1] if len(argv) > 1 else "long"]
        print("opened", bot.try_open_futures(side, float(argv[2]) if len(argv) > 2 else 1.0))
    elif cmd == "kill":
        open(s.kill_file, "w").write("manual kill\n")
        bot.flatten("manual_kill")
        print("kill switch ON, all baskets closed")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
