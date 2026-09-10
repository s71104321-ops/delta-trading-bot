import os
from flask import Flask, request, jsonify
# Import your Delta Exchange client libraries here (e.g., ccxt or delta_rest_client)

app = Flask(__name__)

# Initialize your Delta Exchange API credentials from Render environment variables
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_API_SECRET")

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "active", "message": "Delta Trading Bot is running"}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # force=True bypasses missing/unsupported content-type headers from TradingView
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        # Extract payload fields safely
        symbol = data.get("symbol", "BTCUSD")
        raw_size = data.get("size", 1)
        side = data.get("side", "buy").lower()
        order_type = data.get("order_type", "market")

        # Robust data cleaning for dynamic TradingView placeholders
        try:
            size = int(float(str(raw_size).replace('"', '').replace("'", "")))
        except (ValueError, TypeError):
            size = 1  # Fallback default size if parsing fails

        print(f"Executing Order -> Symbol: {symbol}, Side: {side}, Size: {size}, Type: {order_type}")

        # --- INSERT YOUR DELTA EXCHANGE API ORDER EXECUTION LOGIC HERE ---
        # Example:
        # response = delta_client.create_order(symbol=symbol, size=size, side=side, order_type=order_type)
        
        return jsonify({
            "status": "success", 
            "message": f"Successfully executed {side} order for {size} contracts of {symbol}"
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
