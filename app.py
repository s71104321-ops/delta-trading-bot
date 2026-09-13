from flask import Flask, request
import json
import os
import requests

app = Flask(__name__)

# --- CONFIGURATION ---
DELTA_API_URL = "https://api.india.delta.exchange"  # or mainnet URL if applicable
API_KEY = os.environ.get("DELTA_API_KEY", "YOUR_API_KEY")
API_SECRET = os.environ.get("DELTA_API_SECRET", "YOUR_API_SECRET")

# Set your fixed target position size here (1 contract)
TARGET_LOT_SIZE = 1 

@app.route('/', methods=['GET'])
def home():
    return "Delta Exchange Webhook Server is running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # 1. Safely parse incoming JSON (handles both raw JSON and string-wrapped payloads)
        data = request.get_json(silent=True)
        
        if not data and request.data:
            raw_data = request.data.decode('utf-8').strip()
            if raw_data.startswith('"') and raw_data.endswith('"'):
                raw_data = raw_data[1:-1]
            data = json.loads(raw_data)
            
        if not data:
            return {"error": "Invalid or empty payload"}, 400

        action = data.get("action") # "buy" or "sell"
        ticker = data.get("alert_name", "BTCUSD.P") # e.g., "DELTAIN:BTCUSD.P"

        print(f"[DELTA] Signal Received -> Ticker: {ticker}, Action: {action}, Target Lot Size: {TARGET_LOT_SIZE}")

        # 2. PLACE ORDER LOGIC ON DELTA EXCHANGE
        # Note: Implement your Delta Exchange API call here. 
        # To handle position reversals correctly:
        # If Current Position is +1 and action is 'sell', you need to sell (1 + TARGET_LOT_SIZE) = 2 contracts.
        
        # Example structure for Delta v2 orders API:
        # endpoint = f"{DELTA_API_URL}/v2/orders"
        # payload = {
        #     "product_id": ..., # map ticker to product ID
        #     "size": calculated_order_size,
        #     "side": action,
        #     "order_type": "market"
        # }
        # response = requests.post(endpoint, json=payload, headers=...)

        return {"status": "success", "action": action, "target_size": TARGET_LOT_SIZE}, 200

    except Exception as e:
        print(f"Webhook Processing Error: {str(e)}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
