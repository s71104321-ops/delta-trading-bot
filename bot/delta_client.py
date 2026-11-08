"""Minimal signed REST client for Delta Exchange India (v2 API).

Signature = HMAC_SHA256(secret, METHOD + timestamp + path + query_string + body)
"""
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger("delta")


class DeltaError(Exception):
    pass


def sign(secret: str, method: str, timestamp: str, path: str, query: str, body: str) -> str:
    msg = method + timestamp + path + query + body
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


class DeltaClient:
    def __init__(self, base_url, api_key="", api_secret="", timeout=10, session=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.http = session or requests.Session()

    # ---------- low level ----------
    def _request(self, method, path, params=None, body=None, auth=False, retries=3):
        query = ("?" + urlencode(params)) if params else ""
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        last = None
        for attempt in range(retries):
            headers = {"Content-Type": "application/json", "User-Agent": "delta-bot-v2"}
            if auth:
                ts = str(int(time.time()))
                headers.update({
                    "api-key": self.api_key,
                    "timestamp": ts,
                    "signature": sign(self.api_secret, method, ts, path, query, payload),
                })
            try:
                r = self.http.request(method, self.base_url + path + query,
                                      data=payload or None, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:  # network blip -> retry
                last = e
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = DeltaError(f"{r.status_code}: {r.text[:200]}")
                time.sleep(1 + attempt)
                continue
            data = r.json()
            if not data.get("success", r.ok):
                raise DeltaError(f"{method} {path} failed: {data.get('error', data)}")
            return data.get("result", data)
        raise DeltaError(f"{method} {path} gave up after {retries} tries: {last}")

    # ---------- public ----------
    def products(self, contract_types="call_options,put_options", states="live"):
        # contract_types: call_options,put_options or perpetual_futures
        return self._request("GET", "/v2/products",
                             {"contract_types": contract_types, "states": states})

    def ticker(self, symbol):
        return self._request("GET", f"/v2/tickers/{symbol}")

    def tickers(self, contract_types="call_options,put_options", underlying=None):
        p = {"contract_types": contract_types}
        if underlying:
            p["underlying_asset_symbols"] = underlying
        return self._request("GET", "/v2/tickers", p)

    def candles(self, symbol, resolution, start, end):
        return self._request("GET", "/v2/history/candles",
                             {"symbol": symbol, "resolution": resolution, "start": start, "end": end})

    # ---------- private ----------
    def wallet(self):
        return self._request("GET", "/v2/wallet/balances", auth=True)

    def positions(self):
        return self._request("GET", "/v2/positions/margined", auth=True)

    def open_orders(self):
        return self._request("GET", "/v2/orders", {"states": "open,pending"}, auth=True)

    def place_order(self, product_id, side, size, order_type="limit_order",
                    limit_price=None, reduce_only=False, client_order_id=None):
        if side not in ("buy", "sell"):
            raise ValueError(f"bad side {side!r}")
        if int(size) <= 0:
            raise ValueError("size must be positive")
        body = {"product_id": int(product_id), "side": side, "size": int(size),
                "order_type": order_type, "reduce_only": reduce_only}
        if order_type == "limit_order":
            if not limit_price or float(limit_price) <= 0:
                raise ValueError("limit order needs a positive limit_price")  # the old bot sent 0
            body["limit_price"] = str(limit_price)
        if client_order_id:
            body["client_order_id"] = client_order_id[:32]
        return self._request("POST", "/v2/orders", body=body, auth=True)

    def place_stop(self, product_id, side, size, stop_price):
        """Exchange-side stop-loss (reduce-only market stop). Protects even if the bot/server dies."""
        if float(stop_price) <= 0:
            raise ValueError("stop_price must be positive")
        body = {"product_id": int(product_id), "side": side, "size": int(size),
                "order_type": "market_order", "stop_order_type": "stop_loss_order",
                "stop_price": str(stop_price), "reduce_only": True}
        return self._request("POST", "/v2/orders", body=body, auth=True)

    def get_order(self, order_id):
        return self._request("GET", f"/v2/orders/{order_id}", auth=True)

    def cancel_order(self, order_id, product_id):
        return self._request("DELETE", "/v2/orders",
                             body={"id": int(order_id), "product_id": int(product_id)}, auth=True)

    def cancel_all(self):
        return self._request("DELETE", "/v2/orders/all", body={}, auth=True)
