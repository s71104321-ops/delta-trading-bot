import os
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

app = Flask(__name__)

# ==========================================
# Delta Exchange API Configuration
# ==========================================
# Best practice: Load keys from Render environment variables
API_KEY = os.getenv("DELTA_API_KEY", "YOUR_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET", "YOUR_API_SECRET")
BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange") # Use testnet URL if testing: https://cdn-ind.testnet.deltaex.org

client = DeltaRestClient(
    base_url=BASE_URL,
    api_key=API_KEY,
    api_secret=API_SECRET
)

# BTCUSD.P Product ID on Delta Exchange (usually product ID 84 or fetched dynamically)
# You can verify your exact product ID via client.get_product('BTCUSD.P')
BTCUSD_PRODUCT_ID = int(os.getenv("BTC_PRODUCT_ID", 84)) 

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Parse incoming JSON payload from TradingView
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        # Dynamically extract fields (Fixes static size bug)
        ticker = data.get("alert_name", "BTCUSD.P")
        action = data.get("action")          # "buy" or "sell"
        size = int(float(data.get("size", 1.0)))  # Ensure integer quantity for contract sizing

        print(f"[DELTA] Signal Received -> Ticker: {ticker}, Action: {action}, Target Lot Size: {size}")

        # ==========================================
        # Execute Order on Delta Exchange API
        # ==========================================
        # Places a market order using the dynamic size sent by TradingView
        order_response = client.place_order(
            product_id=BTCUSD_PRODUCT_ID,
            size=size,
            side=action, # "buy" or "sell"
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC
        )

        print(f"[DELTA] Order Success: {order_response}")
        return jsonify({"status": "success", "action": action, "size": size, "response": order_response}), 200

    except Exception as e:
        print(f"[ERROR] Order execution failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
