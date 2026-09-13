from flask import Flask, request
import json
import os
# import requests # Uncomment if you are using 'requests' library to call Delta Exchange API

app = Flask(__name__)

@app.route('/', methods=['GET'])
def home():
    return "Delta Exchange Webhook Server is running!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Step 1: Safely parse incoming data (handles both raw JSON and string-wrapped payloads)
        data = request.get_json(silent=True)
        
        if not data and request.data:
            raw_data = request.data.decode('utf-8').strip()
            # Strip outer quotes if TradingView sends them wrapped
            if raw_data.startswith('"') and raw_data.endswith('"'):
                raw_data = raw_data[1:-1]
            data = json.loads(raw_data)
            
        if not data:
            return {"error": "Invalid or empty payload"}, 400

        action = data.get("action") # "buy" or "sell"
        ticker = data.get("alert_name") # e.g., "DELTAIN:BTCUSD.P"

        print(f"Signal Received -> Ticker: {ticker}, Action: {action}")

        # --- STEP 2: YOUR DELTA EXCHANGE ORDER EXECUTION LOGIC ---
        # Example:
        # if action == "buy":
        #     # Code to place buy order (Lot size: 1)
        # elif action == "sell":
        #     # Code to place sell/short order (Lot size: 1)

        return {"status": "success", "action": action}, 200

    except Exception as e:
        print(f"Webhook Processing Error: {str(e)}")
        return {"error": str(e)}, 400

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
