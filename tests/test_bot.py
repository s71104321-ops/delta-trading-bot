import datetime as dt
import os

import pytest

from bot import config
from bot.basket import BasketError, BasketExecutor, OptionChain, net_premium
from bot.delta_client import DeltaClient, sign
from bot.journal import Journal
from bot.main import Bot
from bot.notifier import Notifier
from bot.risk import RiskBlocked, RiskManager
from bot.strategies import TEMPLATES


class FakeClient:
    """Fake Delta exchange: BTC options chain around spot 75,000."""

    def __init__(self, spot=75000.0, spread=10.0):
        self.spot = spot
        self.spread = spread
        self.marks = {}
        self.orders = []
        exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).isoformat()
        self._products = []
        pid = 1
        for k in range(70000, 80001, 1000):
            for kind in ("call", "put"):
                self._products.append({
                    "id": pid, "symbol": f"{kind[0].upper()}-BTC-{k}", "strike_price": str(k),
                    "contract_type": f"{kind}_options", "settlement_time": exp,
                    "contract_value": "0.001", "underlying_asset": {"symbol": "BTC"},
                })
                pid += 1

    funding = 0.01

    def products(self, contract_types="call_options,put_options", **k):
        if contract_types == "perpetual_futures":
            return [{"id": 999, "symbol": "BTCUSD", "contract_value": "0.001"}]
        return self._products

    def ticker(self, symbol):
        if symbol == "BTCUSD":
            return {"spot_price": self.spot, "mark_price": self.spot, "funding_rate": self.funding,
                    "quotes": {"best_bid": self.spot - 0.5, "best_ask": self.spot + 0.5}}
        k = float(symbol.split("-")[2])
        intrinsic = max(0, self.spot - k) if symbol[0] == "C" else max(0, k - self.spot)
        mid = self.marks.get(symbol, intrinsic + 800)
        return {"mark_price": mid, "quotes": {"best_bid": mid - self.spread / 2,
                                              "best_ask": mid + self.spread / 2}}

    def place_order(self, *a, **k):
        self.orders.append((a, k))
        return {"id": len(self.orders)}

    def cancel_all(self):
        pass


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in list(os.environ):
        if k.startswith(("DELTA_", "LIVE_", "DRY_")):
            monkeypatch.delenv(k)
    return config.load()


def make_bot(settings, client=None):
    b = Bot(settings)
    b._wire(client or FakeClient())
    return b


# ---------- config safety ----------
def test_defaults_are_safe(settings):
    assert settings.env == "testnet" and settings.dry_run


def test_live_needs_confirm_phrase(monkeypatch):
    monkeypatch.setenv("DELTA_ENV", "live")
    with pytest.raises(SystemExit):
        config.load()


def test_real_orders_need_keys(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("DELTA_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        config.load()


# ---------- client ----------
def test_signature_matches_hmac_of_concatenation():
    import hashlib
    import hmac
    expected = hmac.new(b"secret", b'POST1700000000/v2/orders?a=1{"b":2}', hashlib.sha256).hexdigest()
    assert sign("secret", "POST", "1700000000", "/v2/orders", "?a=1", '{"b":2}') == expected


def test_limit_order_with_zero_price_refused():
    c = DeltaClient("https://x")
    with pytest.raises(ValueError):
        c.place_order(1, "buy", 1, "limit_order", 0)   # the bug in the old bot
    with pytest.raises(ValueError):
        c.place_order(1, "hold", 1, "market_order")


# ---------- chain + premium ----------
def test_bull_call_spread_resolves_atm(settings):
    fc = FakeClient()
    ch = OptionChain(fc, "BTC")
    _, chain = ch.load()
    legs = ch.resolve(TEMPLATES["bull_call_spread"], 75000, chain)
    assert [(l["side"], l["strike"]) for l in legs] == [("buy", 75000), ("sell", 77000)]
    assert net_premium(legs) > 0   # debit spread costs money


# ---------- risk ----------
def leg(side, kind, size=1, spread=1):
    return {"symbol": "X", "side": side, "type": kind, "size": size, "spread_pct": spread}


def test_risk_blocks_naked_short(settings):
    r = RiskManager(settings, Journal(":memory:"))
    with pytest.raises(RiskBlocked):
        r.check_new_basket([leg("sell", "call")], -10)
    assert r.check_new_basket([leg("buy", "call"), leg("sell", "call")], 5)


def test_risk_size_spread_premium(settings):
    r = RiskManager(settings, Journal(":memory:"))
    for legs, prem in [([leg("buy", "call", size=99)], 1),
                       ([leg("buy", "call", spread=50)], 1),
                       ([leg("buy", "call")], 10_000)]:
        with pytest.raises(RiskBlocked):
            r.check_new_basket(legs, prem)


def test_kill_switch_blocks(settings):
    open(settings.kill_file, "w").write("x")
    r = RiskManager(settings, Journal(":memory:"))
    with pytest.raises(RiskBlocked):
        r.check_new_basket([leg("buy", "call")], 1)


# ---------- execution ----------
def test_dry_run_open_sends_no_orders_and_journals(settings):
    b = make_bot(settings)
    bid = b.try_open("iron_condor")
    assert b.c.orders == []
    assert b.j.open_baskets()[0]["id"] == bid


def test_buys_execute_before_sells(settings):
    j = Journal(":memory:")
    order = []
    exe = BasketExecutor(FakeClient(), j, Notifier())
    exe._fill = lambda l, **k: order.append(l["side"]) or {"filled": True, "price": l["price"]}
    legs = [leg("sell", "call") | {"price": 1, "contract_value": 1},
            leg("buy", "call") | {"price": 2, "contract_value": 1}]
    exe.open("t", legs)
    assert order == ["buy", "sell"]


def test_failed_leg_unwinds_and_records_nothing(settings):
    j = Journal(":memory:")
    exe = BasketExecutor(FakeClient(), j, Notifier())
    unwound = []
    exe._unwind = lambda legs: unwound.extend(legs)
    calls = iter([{"filled": True, "price": 1}, {"filled": False}])
    exe._fill = lambda l, **k: next(calls)
    legs = [leg("buy", "call") | {"price": 1, "contract_value": 1, "symbol": "A"},
            leg("sell", "call") | {"price": 1, "contract_value": 1, "symbol": "B"}]
    with pytest.raises(BasketError):
        exe.open("t", legs)
    assert [l["symbol"] for l in unwound] == ["A"] and j.open_baskets() == []


def test_basket_stop_loss_closes(settings):
    fc = FakeClient()
    b = make_bot(settings, fc)
    b.try_open("long_straddle")
    for p in fc.products():          # options collapse in value -> big loss
        fc.marks[p["symbol"]] = 1
    b.tick()
    assert b.j.open_baskets() == []


def test_daily_cap_triggers_kill(settings, monkeypatch):
    monkeypatch.setattr(settings, "max_loss_per_basket_usd", 1e9)
    monkeypatch.setattr(settings, "max_daily_loss_usd", 1.0)
    fc = FakeClient()
    b = make_bot(settings, fc)
    b.try_open("long_straddle")
    for p in fc.products():
        fc.marks[p["symbol"]] = 1
    assert b.tick() == "daily_cap"
    assert os.path.exists(settings.kill_file) and b.j.open_baskets() == []


# ---------- futures ----------
from bot.futures import FuturesError  # noqa: E402


def test_futures_size_from_risk(settings):
    b = make_bot(settings)
    leg = b.fut.plan("buy", 1.0, equity_usd=500)
    # stop distance ~750 USD * 0.001 = 0.75 USD/contract -> 5 USD risk => 6 contracts
    assert leg["size"] == 6 and leg["stop"] < leg["price"]
    assert leg["risk_usd"] <= settings.risk_per_trade_usd


def test_futures_leverage_cap(settings, monkeypatch):
    monkeypatch.setattr(settings, "risk_per_trade_usd", 1000)
    b = make_bot(settings)
    leg = b.fut.plan("buy", 1.0, equity_usd=100)   # 3x of 100 USD = 300 USD notional = 4 contracts
    assert leg["notional"] <= 100 * settings.max_leverage


def test_futures_tiny_equity_refused(settings):
    b = make_bot(settings)
    with pytest.raises(FuturesError):
        b.fut.plan("buy", 1.0, equity_usd=10)


def test_futures_funding_filter(settings):
    fc = FakeClient()
    fc.funding = 0.2
    b = make_bot(settings, fc)
    with pytest.raises(FuturesError):
        b.fut.plan("buy", 1.0, 500)
    assert b.fut.plan("sell", 1.0, 500)   # shorts receive that funding


def test_futures_stop_closes_and_only_one_open(settings):
    fc = FakeClient()
    b = make_bot(settings, fc)
    b.try_open_futures("buy", 1.0)
    with pytest.raises(RiskBlocked):
        b.try_open_futures("sell", 1.0)
    fc.spot = 74000                 # price falls through the stop
    b.tick()
    assert b.j.open_baskets() == []
    assert b.j.realized_pnl_since(0) < 0


def test_futures_and_baskets_share_daily_cap(settings, monkeypatch):
    monkeypatch.setattr(settings, "max_daily_loss_usd", 3.0)
    fc = FakeClient()
    b = make_bot(settings, fc)
    b.try_open_futures("buy", 1.0)
    fc.spot = 74000
    b.tick()                        # ~5 USD loss realised
    with pytest.raises(RiskBlocked):
        b.try_open("long_straddle")
