"""Option basket orders: several legs opened/closed together, all-or-nothing.

Legs are described relative to the at-the-money strike, e.g. a bull call spread:
    [LegSpec("call", "buy", 0), LegSpec("call", "sell", +2)]
Order of execution: BUY legs first, then SELL legs, so a short leg is never
open without its protecting long leg. If any leg fails, filled legs are unwound.
"""
import datetime as dt
import logging
import time
import uuid
from dataclasses import dataclass

log = logging.getLogger("basket")


@dataclass
class LegSpec:
    type: str          # "call" | "put"
    side: str          # "buy" | "sell"
    strike_offset: int  # strikes away from ATM (+ = higher strike)
    size: int = 1


class BasketError(Exception):
    pass


def _expiry(p):
    return dt.datetime.fromisoformat(p["settlement_time"].replace("Z", "+00:00"))


class OptionChain:
    def __init__(self, client, underlying):
        self.c = client
        self.underlying = underlying

    def load(self, min_days=1.0, max_days=10.0):
        """Return (expiry, {type: {strike: product}}) for the nearest expiry in range."""
        now = dt.datetime.now(dt.timezone.utc)
        prods = [p for p in self.c.products()
                 if p.get("underlying_asset", {}).get("symbol") == self.underlying]
        by_exp = {}
        for p in prods:
            days = (_expiry(p) - now).total_seconds() / 86400
            if min_days <= days <= max_days:
                by_exp.setdefault(_expiry(p), []).append(p)
        if not by_exp:
            raise BasketError(f"no {self.underlying} expiry between {min_days} and {max_days} days")
        exp = min(by_exp)
        chain = {"call": {}, "put": {}}
        for p in by_exp[exp]:
            kind = "call" if p["contract_type"] == "call_options" else "put"
            chain[kind][float(p["strike_price"])] = p
        return exp, chain

    def resolve(self, specs, spot, chain):
        """Turn LegSpecs into concrete legs with live prices."""
        legs = []
        for s in specs:
            strikes = sorted(chain[s.type])
            atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
            idx = atm + s.strike_offset
            if not 0 <= idx < len(strikes):
                raise BasketError(f"strike offset {s.strike_offset} out of range")
            p = chain[s.type][strikes[idx]]
            t = self.c.ticker(p["symbol"])
            q = t.get("quotes") or {}
            bid, ask = float(q.get("best_bid") or 0), float(q.get("best_ask") or 0)
            if bid <= 0 or ask <= 0:
                raise BasketError(f"{p['symbol']} has no two-sided quote")
            mid = (bid + ask) / 2
            legs.append({
                "symbol": p["symbol"], "product_id": p["id"], "type": s.type, "side": s.side,
                "strike": strikes[idx], "size": s.size, "bid": bid, "ask": ask,
                "spread_pct": (ask - bid) / mid * 100,
                "contract_value": float(p.get("contract_value", 1)),
                "price": ask if s.side == "buy" else bid,   # cross the spread to get filled
            })
        return legs


def net_premium(legs):
    """USD paid (+) or received (-) for the basket."""
    total = 0.0
    for l in legs:
        cash = l["price"] * l["size"] * l["contract_value"]
        total += cash if l["side"] == "buy" else -cash
    return total


class BasketExecutor:
    def __init__(self, client, journal, notifier, dry_run=True, fill_timeout=20):
        self.c, self.j, self.n = client, journal, notifier
        self.dry_run = dry_run
        self.fill_timeout = fill_timeout

    def _fill(self, leg, reduce_only=False, side=None):
        side = side or leg["side"]
        price = leg["price"] if side == leg["side"] else (leg["bid"] if side == "sell" else leg["ask"])
        cid = "b" + uuid.uuid4().hex[:20]
        self.j.event("order", symbol=leg["symbol"], side=side, size=leg["size"],
                     price=price, reduce_only=reduce_only, dry_run=self.dry_run)
        if self.dry_run:
            return {"filled": True, "price": price}
        o = self.c.place_order(leg["product_id"], side, leg["size"], "limit_order",
                               price, reduce_only=reduce_only, client_order_id=cid)
        deadline = time.time() + self.fill_timeout
        while time.time() < deadline:
            st = self.c.get_order(o["id"])
            if st.get("state") == "closed" and int(st.get("unfilled_size", 0)) == 0:
                return {"filled": True, "price": float(st.get("average_fill_price") or price)}
            time.sleep(1)
        self.c.cancel_order(o["id"], leg["product_id"])
        st = self.c.get_order(o["id"])
        filled = int(st.get("size", leg["size"])) - int(st.get("unfilled_size", leg["size"]))
        if filled:  # partial fill: undo it so legs stay balanced
            self.c.place_order(leg["product_id"], "sell" if side == "buy" else "buy", filled,
                               "market_order", reduce_only=True)
        return {"filled": False}

    def open(self, name, legs):
        ordered = sorted(legs, key=lambda l: 0 if l["side"] == "buy" else 1)
        done = []
        for leg in ordered:
            res = self._fill(leg)
            if not res["filled"]:
                self.n.send(f"⚠️ {name}: {leg['symbol']} did not fill, unwinding {len(done)} legs")
                self._unwind(done)
                raise BasketError(f"leg {leg['symbol']} not filled; basket cancelled")
            leg["fill_price"] = res["price"]
            done.append(leg)
        bid = uuid.uuid4().hex[:12]
        cost = net_premium([{**l, "price": l["fill_price"]} for l in done])
        self.j.open_basket(bid, name, done, cost)
        self.n.send(f"✅ Opened {name} ({bid}) net premium {cost:+.2f} USD"
                    f"{' [DRY RUN]' if self.dry_run else ''}")
        return bid

    def _unwind(self, legs):
        # close shorts first (reverse order), so we're never naked short
        for leg in reversed(legs):
            close_side = "sell" if leg["side"] == "buy" else "buy"
            if self.dry_run:
                self.j.event("unwind", symbol=leg["symbol"], dry_run=True)
                continue
            try:
                self.c.place_order(leg["product_id"], close_side, leg["size"], "market_order",
                                   reduce_only=True)
            except Exception as e:  # noqa: BLE001
                self.n.send(f"🚨 UNWIND FAILED {leg['symbol']}: {e} — check Delta manually!")

    def close(self, basket, reason):
        """Close all legs of an open basket at current prices; returns realized P&L."""
        value = 0.0
        for leg in sorted(basket["legs"], key=lambda l: 0 if l["side"] == "sell" else 1):
            t = self.c.ticker(leg["symbol"])
            q = t.get("quotes") or {}
            close_side = "sell" if leg["side"] == "buy" else "buy"
            px = float(q.get("best_bid" if close_side == "sell" else "best_ask") or t.get("mark_price") or 0)
            self.j.event("close_leg", symbol=leg["symbol"], side=close_side, price=px, reason=reason)
            if not self.dry_run:
                self.c.place_order(leg["product_id"], close_side, leg["size"], "market_order",
                                   reduce_only=True)
            cash = px * leg["size"] * leg["contract_value"]
            value += cash if close_side == "sell" else -cash
        pnl = value - basket["entry_cost"]
        self.j.close_basket(basket["id"], value, pnl)
        self.n.send(f"🔒 Closed {basket['name']} ({basket['id']}) reason={reason} P&L {pnl:+.2f} USD")
        return pnl

    def mark_pnl(self, basket):
        value = 0.0
        for leg in basket["legs"]:
            mark = float(self.c.ticker(leg["symbol"]).get("mark_price") or 0)
            cash = mark * leg["size"] * leg["contract_value"]
            value += cash if leg["side"] == "buy" else -cash
        return value - basket["entry_cost"]
