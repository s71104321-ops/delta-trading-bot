from flask import Flask, request, jsonify
import os
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

app = Flask(__name__)

# Initialize your Delta Exchange client using environment variables
API_KEY = os.getenv('API_KEY', 'your_api_key_here')
API_SECRET = os.getenv('API_SECRET', 'your_api_secret_here')
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

        # CRITICAL FIX: Force order size to always be a positive integer to avoid negative_order_size error
        try:
            order_size = abs(int(raw_size))
        except (ValueError, TypeError):
            order_size = 1

        # Map action to side format expected by Delta Exchange ('buy' or 'sell')
        side = 'buy' if action == 'buy' else 'sell'

        # Optional: Fetch product ID dynamically or use your hardcoded product ID integer for BTCUSD
        # product_id = ... 

        print(f"Received Webhook -> Action: {side}, Contract: {contract}, Size: {order_size}")

        # Example order placement call (uncomment and adjust product_id as per your setup):
        # order_response = delta_client.place_order(
        #     product_id=product_id, 
        #     size=order_size, 
        #     side=side, 
        #     order_type=OrderType.MARKET
        # )

        return jsonify({
            "success": True, 
            "action": side, 
            "contract": contract, 
            "size": order_size
        }), 200

    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
