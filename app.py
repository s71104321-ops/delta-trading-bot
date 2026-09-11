import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
BASE_URL = os.getenv("BASE_URL", "https://api.india.delta.exchange")

def generate_signature(secret, method, path, query_string="", payload_string=""):
    message = method + path + query_string + payload_string
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Invalid JSON payload", "status": "error"}), 400

    incoming_secret = data.get('webhook_secret')
    if incoming_secret != WEBHOOK_SECRET:
        return jsonify({"message": "Unauthorized", "status": "error"}), 403

    ticker = data.get('ticker', 'BTCUSDT').upper()
    action = data.get('action', '').lower()
    contracts = int(data.get('contracts', 1))

    # Map product ID or symbol (Delta India product ids can be queried, or we map BTCUSDT to product_id)
    # For BTC-PERP or standard USDT contracts on Delta, product_id for BTCUSDT is typically fetched or mapped.
    # Let's map common ones or resolve dynamically:
    try:
        # 1. Fetch products list from Delta India to get the exact product_id for the ticker
        products_url = f"{BASE_URL}/v2/products"
        resp = requests.get(products_url)
        products = resp.json().get('result', [])
        
        product_id = None
        target_symbol = ticker.replace("/", "").replace("-", "")
        for p in products:
            p_symbol = p.get('symbol', '').replace("/", "").replace("-", "")
            if p_symbol == target_symbol or target_symbol in p_symbol:
                product_id = p.get('id')
                break
        
        if not product_id:
            return jsonify({"message": f"Delta native product not found for {ticker}", "status": "error"}), 400

        # 2. Prepare Order Payload
        path = "/v2/orders"
        method = "POST"
        
        payload = {
            "product_id": int(product_id),
            "size": contracts,
            "side": action, # 'buy' or 'sell'
            "order_type": "market"
        }
        
        import json
        payload_string = json.dumps(payload)
        timestamp = str(int(time.time()))
        
        # Delta signature format includes timestamp depending on endpoint version, 
        # using native headers:
        signature = generate_signature(API_SECRET, method, path, "", payload_string)
        
        headers = {
            "api-key": API_KEY,
            "signature": signature,
            "timestamp": timestamp,
            "Content-Type": "application/json"
        }

        order_resp = requests.post(BASE_URL + path, headers=headers, data=payload_string)
        res_data = order_resp.json()

        if order_resp.status_code == 200 and res_data.get('success'):
            print(f"Order Executed Successfully: {res_data}")
            return jsonify({"message": "Order executed successfully", "data": res_data, "status": "success"}), 200
        else:
            print(f"Delta API Error: {res_data}")
            return jsonify({"message": res_data, "status": "error"}), 400

    except Exception as e:
        print(f"Execution Error: {str(e)}")
        return jsonify({"message": str(e), "status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
