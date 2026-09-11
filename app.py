import os
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

app = Flask(__name__)

# Check multiple possible environment variable names to catch any naming mismatch
DELTA_API_KEY = os.getenv('DELTA_API_KEY') or os.getenv('API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET') or os.getenv('API_SECRET')

# Debug logs to help diagnose without exposing secrets
print(f"DEBUG: API Key loaded? {bool(DELTA_API_KEY)}")
print(f"DEBUG: API Secret loaded? {bool(DELTA_API_SECRET)}")

BASE_URL = "https://api.india.delta.exchange"

# Initialize Delta client only if keys exist to prevent crashing on boot
delta_client = None
if DELTA_API_KEY and DELTA_API_SECRET:
    delta_client = DeltaRestClient(
        base_url=BASE_URL,
        api_key=DELTA_API_KEY,
        api_secret=DELTA_API_SECRET
    )

BTC_PRODUCT_ID = 27  # Update with your specific product ID if needed

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        global delta_client
        if not delta_client:
            # Re-check in case environment variables populated late
            k = os.getenv('DELTA_API_KEY') or os.getenv('API_KEY')
            s = os.getenv('DELTA_API_SECRET') or os.getenv('API_SECRET')
            if k and s:
                delta_client = DeltaRestClient(base_url=BASE_URL, api_key=k, api_secret=s)
            else:
                return jsonify({"status": "error", "message": "Api_key or Api_secret missing"}), 400

        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        alert_name = data.get('alert_name', 'Unknown Alert')
        action = data.get('action')          # 'buy' or 'sell'
        market_pos = data.get('market_position') # 'long', 'short', or 'flat'
        contracts = float(data.get('contracts', 0))
        price = data.get('price')

        print(f"[{alert_name}] Signal Received -> Action: {action}, Position: {market_pos}, Contracts: {contracts}, Price: {price}")

        # --- STEP 1: AUTO-CANCEL STALE ORDERS ---
        try:
            open_orders = delta_client.get_live_orders(product_id=BTC_PRODUCT_ID)
            for order in open_orders:
                delta_client.cancel_order(product_id=BTC_PRODUCT_ID, order_id=order['id'])
        except Exception as cancel_err:
            print(f"Order cancellation warning: {str(cancel_err)}")

        # --- STEP 2: AUTO-FILL & REVERSE EXECUTION ---
        order_side = 'buy' if action == 'buy' else 'sell'
        
        order_response = delta_client.place_order(
            product_id=BTC_PRODUCT_ID,
            size=int(contracts),
            side=order_side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC
        )

        return jsonify({
            "status": "success",
            "message": f"Successfully executed {order_side} market order for {contracts} contracts.",
            "exchange_response": order_response
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "bot": "Delta Exchange DPO RMA Bot"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
