import os
import requests
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType

app = Flask(__name__)

# Fetch API credentials from Render Environment Variables
API_KEY = os.environ.get("DELTA_API_KEY")
API_SECRET = os.environ.get("DELTA_API_SECRET")

# Initialize Delta Exchange India REST Client
delta_client = DeltaRestClient(
    base_url="https://api.india.delta.exchange",
    api_key=API_KEY,
    api_secret=API_SECRET
)

@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Delta Trading Bot is running live!"}), 200

@app.route("/get-ip", methods=["GET"])
def get_ip():
    """Returns the public egress IP of this Render bot instance for IP whitelisting."""
    try:
        response = requests.get("https://api.ipify.org?format=json")
        return response.json(), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    """Receives JSON payloads from TradingView alerts and executes trades via delta-rest-client."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload received"}), 400

        # Support both product_id or symbol mapping from TradingView
        symbol_map = {"BTCUSD": 27}
        
        product_id = data.get("product_id")
        if not product_id and "symbol" in data:
            sym = str(data.get("symbol")).upper()
            product_id = symbol_map.get(sym, 27) # defaults to 27 for BTCUSD
            
        product_id = int(product_id)
        size = int(data.get("size", 1))
        side = str(data.get("side", "buy")).lower()
        order_type_str = str(data.get("order_type", "market")).lower()

        print(f"Executing Order -> Product ID: {product_id}, Side: {side}, Size: {size}, Type: {order_type_str}")

        # Execute order on Delta Exchange using proper OrderType objects
        if order_type_str == "market":
            order_response = delta_client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                order_type=OrderType.MARKET
            )
        else:
            price = str(data.get("price"))
            order_response = delta_client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                limit_price=price,
                order_type=OrderType.LIMIT
            )

        return jsonify({
            "status": "success", 
            "message": "Order executed successfully on Delta Exchange",
            "delta_response": order_response
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
