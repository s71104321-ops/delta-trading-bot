import os
import requests
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, create_order_format

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

        # Extract parameters sent from TradingView alert message
        product_id = int(data.get("product_id"))  # e.g., product id from Delta Exchange
        size = int(data.get("size", 1))
        side = data.get("side", "buy")            # "buy" or "sell"
        order_type = data.get("order_type", "market")

        print(f"Received Webhook -> Product ID: {product_id}, Side: {side}, Size: {size}, Type: {order_type}")

        # Execute order on Delta Exchange India using delta-rest-client
        if order_type.lower() == "market":
            # Place market order format or direct call depending on client wrapper version
            order_response = delta_client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                order_type="market"
            )
        else:
            price = data.get("price")
            order_response = delta_client.place_order(
                product_id=product_id,
                size=size,
                side=side,
                limit_price=str(price),
                order_type="limit"
            )

        return jsonify({
            "status": "success", 
            "message": "Order executed successfully on Delta",
            "delta_response": order_response
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
