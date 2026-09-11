import os
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

# Load environment variables configured on Render
API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Initialize CCXT exchange for Delta India with explicit production URL routing
exchange = ccxt.delta({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'  # Targets derivatives/perpetuals
    },
    'urls': {
        'api': {
            'public': 'https://api.india.delta.exchange',
            'private': 'https://api.india.delta.exchange',
        }
    }
})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    if not data:
        return jsonify({"message": "Invalid JSON payload", "status": "error"}), 400

    # Verify webhook secret authorization
    incoming_secret = data.get('webhook_secret')
    if incoming_secret != WEBHOOK_SECRET:
        return jsonify({"message": "Unauthorized", "status": "error"}), 403

    # Extract alert parameters
    ticker = data.get('ticker', 'BTCUSDT').upper()
    action = data.get('action', '').lower()  # 'buy' or 'sell'
    contracts = float(data.get('contracts', 1))

    try:
        # Load exchange markets dynamically so CCXT recognizes trading pairs
        exchange.load_markets()

        # Map raw ticker to CCXT unified perpetual futures format (e.g., BTC/USDT:USDT)
        if "/" not in ticker:
            if ticker.endswith("USDT"):
                base = ticker[:-4]
                symbol = f"{base}/USDT:USDT"
            else:
                symbol = f"{ticker}/USDT:USDT"
        else:
            symbol = ticker

        # Verify symbol exists in loaded markets
        if symbol not in exchange.markets:
            return jsonify({"message": f"Delta does not have market symbol {symbol}", "status": "error"}), 400

        # Execute market order
        if action == 'buy':
            order = exchange.create_market_order(symbol, 'buy', contracts)
            print(f"1-Lot LONG Executed: {order}")
        elif action == 'sell':
            order = exchange.create_market_order(symbol, 'sell', contracts)
            print(f"1-Lot SHORT Executed: {order}")
        else:
            return jsonify({"message": f"Unknown action: {action}", "status": "error"}), 400

        return jsonify({"message": "Order executed successfully", "status": "success"}), 200

    except Exception as e:
        print(f"Execution Error: {str(e)}")
        return jsonify({"message": str(e), "status": "error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
