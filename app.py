import os
import time
import hmac
import hashlib
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Automatically strip whitespace/newlines from keys pasted into Render
API_KEY = os.getenv("DELTA_API_KEY", "").strip()
API_SECRET = os.getenv("DELTA_SECRET_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
BASE_URL = os.getenv("BASE_URL", "https://api.india.delta.exchange")

def generate_signature(method, endpoint, query_string, payload_string, timestamp, secret):
    # Delta India precise signature format: method + timestamp + endpoint + query_string + payload_string
    signature_data = method + timestamp + endpoint + query_string + payload_string
    print(f"DEBUG Signature Data: {signature_data}") # Visible in Render logs
    message = bytes(signature_data, 'utf-8')
    secret_bytes = bytes(secret, 'utf-8')
    hash_obj = hmac.new(secret_bytes, message, hashlib.sha256)
    return hash_obj.hexdigest()

@app.route('/my-ip', methods=['GET'])
def get_ip():
    """Diagnostic route to check Render's outgoing public IP address"""
    try:
        ip = requests.get('https://api.ipify.org', timeout=5).text
        return jsonify({"bot_ip": ip, "status": "success"}), 200
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"}), 500

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

    try:
        # 1. Fetch products list from Delta India to get the correct product_id
        products_url = f"{BASE_URL}/v2/products"
        resp = requests.get(products_url)
        products = resp.json().get('result', [])
        
        product_id = None
        base_asset = ticker.replace("USDT", "").replace("/", "").replace("-", "")
        
        for p in products:
            p_symbol = p.get('symbol', '').upper()
            if p_symbol.startswith(base_asset):
                product_id = p.get('id')
                break
        
        # Fallback default product ID for Bitcoin Perpetual if name matching fails
        if not product_id and "BTC" in base_asset:
            product_id = 117569  

        if not product_id:
            return jsonify({"message": f"Delta native product not found for {ticker}", "status": "error"}), 400

        # 2. Prepare Order Payload
        path = "/v2/orders"
        method = "POST"
        
        payload = {
            "product_id": int(product_id),
            "size": contracts,
            "side": action, # 'buy' or 'sell'
            "order_type": "market_order"
        }
        
        # CRUCIAL: Compact serialization with zero spaces to match Delta's signature parser
        payload_string = json.dumps(payload, separators=(',', ':'))
        timestamp = str(int(time.time()))
        
        signature = generate_signature(method, path, "", payload_string, timestamp, API_SECRET)
        
        headers = {
            "api-key": API_KEY,
            "signature": signature,
            "timestamp": timestamp,
            "Content-Type": "application/json"
        }

        # Pass payload_string explicitly to data= so spaces aren't re-added
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
