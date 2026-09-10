import os
import requests
from flask import Flask, request, jsonify
# Import your delta exchange client library as needed
# from delta_rest_client import DeltaRestClient

app = Flask(__name__)

# Fetch API credentials from Render Environment Variables
API_KEY = os.environ.get("DELTA_API_KEY")
API_SECRET = os.environ.get("DELTA_API_SECRET")

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
    """Receives JSON payloads from TradingView alerts and executes trades."""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON payload received"}), 400

        # Extract parameters sent from TradingView
        symbol = data.get("symbol", "BTCUSD")
        size = data.get("size", 1)
        side = data.get("side", "buy")
        order_type = data.get("order_type", "market")

        print(f"Received Alert -> Symbol: {symbol}, Side: {side}, Size: {size}, Type: {order_type}")

        # TODO: Initialize your Delta REST client here using API_KEY and API_SECRET
        # client = DeltaRestClient(base_url="https://api.india.delta.exchange", api_key=API_KEY, api_secret=API_SECRET)
        # response = client.create_order(product_id=..., size=size, side=side, order_type=order_type)

        return jsonify({"status": "success", "message": "Order processed successfully"}), 200

    except Exception as e:
        print(f"Webhook Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
