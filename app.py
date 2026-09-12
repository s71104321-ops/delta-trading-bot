import os
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

app = Flask(__name__)

DELTA_API_KEY = os.getenv('DELTA_API_KEY') or os.getenv('API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET') or os.getenv('API_SECRET')

BASE_URL = "https://api.india.delta.exchange"

delta_client = None
if DELTA_API_KEY and DELTA_API_SECRET:
    delta_client = DeltaRestClient(
        base_url=BASE_URL,
        api_key=DELTA_API_KEY,
        api_secret=DELTA_API_SECRET
    )

BTC_PRODUCT_ID = 27  # BTCUSD product ID
FIXED_LOT_SIZE = 4   # Always trade 4 contracts on entry, 8 on reversal

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        global delta_client
        if not delta_client:
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
        action = data.get('action')  # 'buy' or 'sell'

        print(f"[{alert_name}] Signal Received -> Action: {action}, Target Lot Size: {FIXED_LOT_SIZE}")

        # --- STEP 1: CANCEL OPEN STALE ORDERS ---
        try:
            open_orders = delta_client.get_live_orders(product_id=BTC_PRODUCT_ID)
            for order in open_orders:
                delta_client.cancel_order(product_id=BTC_PRODUCT_ID, order_id=order['id'])
        except Exception as cancel_err:
            print(f"Order cancellation warning: {str(cancel_err)}")

        # --- STEP 2: CHECK CURRENT POSITION ---
        current_position_size = 0
        try:
            position = delta_client.get_position(product_id=BTC_PRODUCT_ID)
            if position and 'size' in position:
                current_position_size = int(position['size'])  # Positive = Long, Negative = Short
        except Exception as pos_err:
            print(f"Position check warning: {str(pos_err)}")

        # --- STEP 3: ENFORCE FIXED SIZING & REVERSAL MATH ---
        target_side = 'buy' if action == 'buy' else 'sell'
        execution_size = FIXED_LOT_SIZE

        if current_position_size != 0:
            is_long = current_position_size > 0
            # If current position direction is opposite to the incoming action, it's a REVERSAL
            if (is_long and action == 'sell') or (not is_long and action == 'buy'):
                # Close existing position (abs value) + open new fixed 4 lots (Results in 8 total contracts)
                execution_size = abs(current_position_size) + FIXED_LOT_SIZE
            else:
                # Same direction signal: check if we already match the fixed lot size
                if abs(current_position_size) >= FIXED_LOT_SIZE:
                    print("Position already matches or exceeds target lot size. Skipping.")
                    return jsonify({"status": "success", "message": "Position already matches target size."}), 200
                else:
                    # Top up if partially filled
                    execution_size = FIXED_LOT_SIZE - abs(current_position_size)

        print(f"Executing {target_side} market order for size: {execution_size} (Current Pos: {current_position_size})")

        order_response = delta_client.place_order(
            product_id=BTC_PRODUCT_ID,
            size=execution_size,
            side=target_side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC
        )

        return jsonify({
            "status": "success",
            "message": f"Successfully executed {target_side} order for {execution_size} contracts.",
            "exchange_response": order_response
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "bot": "Delta Exchange Fixed-Lot Bot"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
