import os
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType

app = Flask(__name__)

# Initialize Delta Exchange Client using Render environment variables
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")

# Use production base URL (or testnet if testing)
delta_client = DeltaRestClient(
    base_url='https://api.india.delta.exchange', 
    api_key=API_KEY, 
    api_secret=API_SECRET
)

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "active", "message": "Delta Trading Bot is running"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # force=True handles TradingView payloads without headers
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        # Extract payload fields
        product_id = int(data.get("product_id", 27))  # Default product ID (e.g., BTCUSD product ID on Delta)
        raw_size = data.get("size", 1)
        side = data.get("side", "buy").lower()

        # Clean size value
        try:
            size = int(float(str(raw_size).replace('"', '').replace("'", "")))
        except (ValueError, TypeError):
            size = 1

        print(f"Placing Order on Delta -> Product ID: {product_id}, Side: {side}, Size: {size}")

        # Execute real market order on Delta Exchange
        order_response = delta_client.place_order(
            product_id=product_id,
            size=size,
            side=side,
            order_type=OrderType.MARKET
        )
        
        return jsonify({
            "status": "success", 
            "delta_response": order_response,
            "message": "Order executed successfully on Delta Exchange"
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
