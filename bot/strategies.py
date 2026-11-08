"""Basket templates (defined-risk only) and the strategy hook.

No trading strategy is switched on yet: the entry rule will come from backtesting.
Until then STRATEGY=none means the bot only monitors, manages exits and logs.
"""
from .basket import LegSpec

TEMPLATES = {
    # direction views, limited risk
    "bull_call_spread": [LegSpec("call", "buy", 0), LegSpec("call", "sell", +2)],
    "bear_put_spread":  [LegSpec("put", "buy", 0), LegSpec("put", "sell", -2)],
    # volatility views
    "long_straddle":    [LegSpec("call", "buy", 0), LegSpec("put", "buy", 0)],
    "long_strangle":    [LegSpec("call", "buy", +2), LegSpec("put", "buy", -2)],
    # range view, income, wings cap the loss
    "iron_condor": [
        LegSpec("put", "buy", -4), LegSpec("put", "sell", -2),
        LegSpec("call", "sell", +2), LegSpec("call", "buy", +4),
    ],
}


class Strategy:
    name = "none"

    def entry_signal(self, market):
        """Return None, a TEMPLATES key (option basket), or
        {"kind": "futures", "side": "buy"|"sell", "stop_pct": 1.0} for a perpetual trade."""
        return None


STRATEGIES = {"none": Strategy}


def get(name):
    if name not in STRATEGIES:
        raise SystemExit(f"unknown STRATEGY {name!r}; options: {list(STRATEGIES)}")
    return STRATEGIES[name]()
