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

def get_btc_product_id():
    try:
        products = client.get_products()
        for p in products.get('result', []):
            if p.get('symbol') == 'BTCUSD.P':
                return p.get('id')
    except Exception as e:
        print(f"[WARNING] Could not fetch products dynamically: {e}")
    # Fallback product ID
    return 84

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Parse incoming JSON payload from TradingView alert
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        ticker = data.get("alert_name", "BTCUSD.P")
        action = data.get("action")          # "buy" or "sell"
        size = int(float(data.get("size", 1.0)))  # Dynamically reads 1 or 2 contracts

        print(f"[DELTA] Signal Received -> Ticker: {ticker}, Action: {action}, Target Lot Size: {size}")

        # Dynamically fetch the correct product ID to prevent 'invalid_contract' errors
        product_id = get_btc_product_id()
        print(f"[DELTA] Using Product ID: {product_id}")

        # Build and execute the order
        order = create_order_format(0, size, action, product_id)
        order_response = client.create_order(order)

        print(f"[DELTA] Order Success: {order_response}")
        return jsonify({"status": "success", "action": action, "size": size, "response": order_response}), 200

    except Exception as e:
        print(f"[ERROR] Order execution failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
