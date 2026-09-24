import os
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, create_order_format

app = Flask(__name__)

# Load Delta API credentials from Render environment variables
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")
BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange")

client = DeltaRestClient(
    base_url=BASE_URL,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# Replace with your specific Delta Exchange product ID for BTCUSD.P (e.g., 84 or fetched)
PRODUCT_ID = int(os.getenv("BTC_PRODUCT_ID", 84))

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Parse incoming JSON payload from TradingView alert
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        # Dynamically read values (Fixes the static size bug)
        ticker = data.get("alert_name", "BTCUSD.P")
        action = data.get("action")          # "buy" or "sell"
        size = int(float(data.get("size", 1.0)))  # Dynamically reads 1 or 2 contracts

        print(f"[DELTA] Signal Received -> Ticker: {ticker}, Action: {action}, Target Lot Size: {size}")

        # Build and execute the order using delta-rest-client
        # For market orders, limit_price can be set to 0 or current price depending on market type, 
        # or use create_order_format for standard execution:
        order = create_order_format(0, size, action, PRODUCT_ID)
        order_response = client.create_order(order)

        print(f"[DELTA] Order Success: {order_response}")
        return jsonify({"status": "success", "action": action, "size": size, "response": order_response}), 200

    except Exception as e:
        print(f"[ERROR] Order execution failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
