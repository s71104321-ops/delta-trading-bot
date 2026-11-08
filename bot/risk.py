"""Risk manager: every basket must pass here before any order is sent."""
import datetime as dt
import os


class RiskBlocked(Exception):
    pass


def ist_day_start_ts():
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    now = dt.datetime.now(ist)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class RiskManager:
    def __init__(self, settings, journal):
        self.s = settings
        self.j = journal

    def kill_switch_on(self):
        return os.path.exists(self.s.kill_file)

    def daily_pnl(self, unrealized=0.0):
        return self.j.realized_pnl_since(ist_day_start_ts()) + unrealized

    def check_new_basket(self, legs, net_premium_usd, unrealized=0.0):
        """legs: list of dicts with size, spread_pct. net_premium_usd: cash paid (+) / received (-)."""
        if self.kill_switch_on():
            raise RiskBlocked("kill switch is ON")
        if len(self._open(perp=False)) >= self.s.max_open_baskets:
            raise RiskBlocked(f"already {self.s.max_open_baskets} baskets open")
        if self.daily_pnl(unrealized) <= -self.s.max_daily_loss_usd:
            raise RiskBlocked(f"daily loss cap {self.s.max_daily_loss_usd} USD reached")
        for leg in legs:
            if leg["size"] > self.s.max_contracts_per_leg:
                raise RiskBlocked(f"leg size {leg['size']} > max {self.s.max_contracts_per_leg}")
            if leg.get("spread_pct", 0) > self.s.max_spread_pct:
                raise RiskBlocked(f"{leg['symbol']} spread {leg['spread_pct']:.1f}% too wide")
        if abs(net_premium_usd) > self.s.max_premium_per_basket_usd:
            raise RiskBlocked(f"basket premium {net_premium_usd:.2f} USD over limit")
        if any(l["side"] == "sell" for l in legs) and not self._short_legs_covered(legs):
            raise RiskBlocked("naked short option leg (unlimited risk) is not allowed")
        return True

    def _open(self, perp):
        return [p for p in self.j.open_baskets() if (p["legs"][0].get("type") == "perp") == perp]

    def check_new_futures(self, leg, unrealized=0.0):
        if self.kill_switch_on():
            raise RiskBlocked("kill switch is ON")
        if len(self._open(perp=True)) >= self.s.max_open_futures:
            raise RiskBlocked(f"already {self.s.max_open_futures} futures position(s) open")
        if self.daily_pnl(unrealized) <= -self.s.max_daily_loss_usd:
            raise RiskBlocked(f"daily loss cap {self.s.max_daily_loss_usd} USD reached")
        if not leg.get("stop"):
            raise RiskBlocked("futures trade without a stop-loss is not allowed")
        if leg["risk_usd"] > self.s.risk_per_trade_usd * 1.01:
            raise RiskBlocked(f"trade risk {leg['risk_usd']:.2f} USD over limit")
        return True

    @staticmethod
    def _short_legs_covered(legs):
        """Each short call/put must be matched by an equal-size long of the same type (defined risk)."""
        for kind in ("call", "put"):
            short = sum(l["size"] for l in legs if l["side"] == "sell" and l["type"] == kind)
            long_ = sum(l["size"] for l in legs if l["side"] == "buy" and l["type"] == kind)
            if short > long_:
                return False
        return True

    def basket_should_exit(self, basket_pnl, entry_cost):
        """Exit at whichever comes first: fixed USD stop, or X% of the basket's premium."""
        pct_stop = abs(entry_cost) * self.s.max_loss_pct_of_premium / 100
        limit = min(self.s.max_loss_per_basket_usd, pct_stop) if pct_stop > 0 else self.s.max_loss_per_basket_usd
        return basket_pnl <= -limit
