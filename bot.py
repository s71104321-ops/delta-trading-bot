from flask import Flask, request, jsonify
import os
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

app = Flask(__name__)

# Initialize your Delta Exchange client using environment variables
API_KEY = os.getenv('API_KEY', '')
API_SECRET = os.getenv('API_SECRET', '')
BASE_URL = os.getenv('BASE_URL', 'https://api.india.delta.exchange')

delta_client = DeltaRestClient(
    base_url=BASE_URL,
    api_key=API_KEY,
    api_secret=API_SECRET
)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No JSON payload received"}), 400

        strategy = data.get('strategy', {})
        action = strategy.get('action', 'buy').lower()  # 'buy' or 'sell'
        contract = strategy.get('contract', 'BTCUSD')
        raw_size = strategy.get('size', 1)

        # Force order size to always be a positive integer to prevent negative size errors
        try:
            order_size = abs(int(raw_size))
        except (ValueError, TypeError):
            order_size = 1

        # Map action to side format expected by Delta Exchange ('buy' or 'sell')
        side = 'buy' if action == 'buy' else 'sell'

        # BTCUSD product ID on Delta Exchange India is typically 27
        product_id = 27 

        print(f"Executing Live Order -> Product ID: {product_id}, Side: {side}, Size: {order_size}")

        # Place the live market order on Delta Exchange
        order_response = delta_client.place_order(
            product_id=product_id, 
            size=order_size, 
            side=side, 
            order_type=OrderType.MARKET
        )

        return jsonify({
            "success": True, 
            "delta_response": order_response
        }), 200

    except Exception as e:
        print(f"Error executing trade: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
