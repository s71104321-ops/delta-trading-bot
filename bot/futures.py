"""Perpetual futures trades (e.g. BTCUSD), sharing the journal and daily loss cap with baskets.

Every trade MUST have a stop-loss. Size is calculated from the stop:
    size = risk_per_trade_usd / (stop distance in USD * contract_value)
then capped by max contracts and max leverage.
A reduce-only stop order is placed on Delta itself, so the position is protected
even if the server goes down. The bot also checks the stop every loop as a backup.
"""
import logging
import math
import time
import uuid

log = logging.getLogger("futures")


class FuturesError(Exception):
    pass


class FuturesTrader:
    def __init__(self, client, journal, notifier, settings, dry_run=True, fill_timeout=10):
        self.c, self.j, self.n, self.s = client, journal, notifier, settings
        self.dry_run = dry_run
        self.fill_timeout = fill_timeout
        self._product = None

    def product(self):
        if self._product is None:
            sym = f"{self.s.underlying}USD"
            prods = self.c.products(contract_types="perpetual_futures")
            match = [p for p in prods if p.get("symbol") == sym]
            if not match:
                raise FuturesError(f"perpetual {sym} not found")   # never guess a product id
            self._product = match[0]
        return self._product

    def quote(self):
        t = self.c.ticker(self.product()["symbol"])
        q = t.get("quotes") or {}
        return {"bid": float(q.get("best_bid") or 0), "ask": float(q.get("best_ask") or 0),
                "mark": float(t.get("mark_price") or 0),
                "funding": float(t.get("funding_rate") or 0)}   # % per funding interval

    def plan(self, side, stop_pct, equity_usd):
        """Work out entry, stop and size for a trade. Raises FuturesError if it breaks a rule."""
        if side not in ("buy", "sell"):
            raise FuturesError(f"bad side {side!r}")
        if not 0.2 <= stop_pct <= 10:
            raise FuturesError("stop_pct must be between 0.2% and 10%")
        q = self.quote()
        if q["bid"] <= 0 or q["ask"] <= 0:
            raise FuturesError("no two-sided quote")
        # funding filter: don't open a trade that pays a large funding rate
        pays = q["funding"] if side == "buy" else -q["funding"]
        if pays > self.s.max_funding_pct:
            raise FuturesError(f"funding {q['funding']:.4f}% too costly for {side}")
        entry = q["ask"] if side == "buy" else q["bid"]
        stop = entry * (1 - stop_pct / 100) if side == "buy" else entry * (1 + stop_pct / 100)
        cv = float(self.product().get("contract_value", 0.001))
        risk_per_contract = abs(entry - stop) * cv
        size = math.floor(self.s.risk_per_trade_usd / risk_per_contract)
        size = min(size, self.s.max_futures_contracts)
        max_by_leverage = math.floor(equity_usd * self.s.max_leverage / (entry * cv))
        size = min(size, max_by_leverage)
        if size < 1:
            raise FuturesError("risk/leverage limits allow less than 1 contract")
        return {"symbol": self.product()["symbol"], "product_id": self.product()["id"],
                "type": "perp", "side": side, "size": size, "price": round(entry, 1),
                "stop": round(stop, 1), "contract_value": cv, "bid": q["bid"], "ask": q["ask"],
                "notional": size * entry * cv, "risk_usd": size * risk_per_contract}

    def open(self, leg, name="perp"):
        self.j.event("order", symbol=leg["symbol"], side=leg["side"], size=leg["size"],
                     price=leg["price"], stop=leg["stop"], dry_run=self.dry_run)
        if not self.dry_run:
            o = self.c.place_order(leg["product_id"], leg["side"], leg["size"], "limit_order",
                                   leg["price"], client_order_id="f" + uuid.uuid4().hex[:20])
            deadline = time.time() + self.fill_timeout
            st = self.c.get_order(o["id"])
            while st.get("state") == "open" and time.time() < deadline:
                time.sleep(1)
                st = self.c.get_order(o["id"])
            filled = int(st.get("size", leg["size"])) - int(st.get("unfilled_size", 0))
            if st.get("state") != "closed" or filled < leg["size"]:
                if st.get("state") == "open":
                    self.c.cancel_order(o["id"], leg["product_id"])
                if filled:
                    self.c.place_order(leg["product_id"], "sell" if leg["side"] == "buy" else "buy",
                                       filled, "market_order", reduce_only=True)
                raise FuturesError("entry not filled immediately; cancelled")
            leg["price"] = float(st.get("average_fill_price") or leg["price"])
            try:
                so = self.c.place_stop(leg["product_id"], "sell" if leg["side"] == "buy" else "buy",
                                       leg["size"], leg["stop"])
                leg["stop_order_id"] = so.get("id")
            except Exception as e:  # no exchange stop -> don't keep the position
                self.c.place_order(leg["product_id"], "sell" if leg["side"] == "buy" else "buy",
                                   leg["size"], "market_order", reduce_only=True)
                self.n.send(f"🚨 Stop order failed ({e}); position closed immediately.")
                raise FuturesError(f"stop order failed: {e}") from e
        cost = leg["price"] * leg["size"] * leg["contract_value"] * (1 if leg["side"] == "buy" else -1)
        pid = uuid.uuid4().hex[:12]
        self.j.open_basket(pid, f"{name}:{leg['side']}", [leg], cost)
        self.n.send(f"✅ {leg['side'].upper()} {leg['size']} {leg['symbol']} @ {leg['price']} "
                    f"stop {leg['stop']} risk {leg['risk_usd']:.2f} USD"
                    f"{' [DRY RUN]' if self.dry_run else ''}")
        return pid

    def cancel_stop(self, position):
        leg = position["legs"][0]
        if not self.dry_run and leg.get("stop_order_id"):
            try:
                self.c.cancel_order(leg["stop_order_id"], leg["product_id"])
            except Exception as e:  # already triggered/cancelled is fine
                log.info("stop cancel: %s", e)

    @staticmethod
    def is_perp(position):
        return position["legs"][0].get("type") == "perp"

    @staticmethod
    def stop_hit(position, mark):
        leg = position["legs"][0]
        return mark <= leg["stop"] if leg["side"] == "buy" else mark >= leg["stop"]
