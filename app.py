import os
from flask import Flask, request, jsonify
import ccxt

app = Flask(__name__)

API_KEY = os.getenv("DELTA_API_KEY")
API_SECRET = os.getenv("DELTA_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

exchange = ccxt.delta({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
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

    incoming_secret = data.get('webhook_secret')
    if incoming_secret != WEBHOOK_SECRET:
        return jsonify({"message": "Unauthorized", "status": "error"}), 403

    ticker = data.get('ticker', 'BTCUSDT').upper()
    action = data.get('action', '').lower()
    contracts = float(data.get('contracts', 1))

    try:
        exchange.load_markets()

        # Find the matching market dynamically from loaded exchange keys
        symbol = None
        for s in exchange.markets.keys():
            clean_s = s.replace("/", "").replace(":", "").replace("-", "")
            if clean_s == ticker or ticker in clean_s:
                symbol = s
                break
        
        # Fallback if exact match wasn't auto-discovered
        if not symbol:
            symbol = "BTC/USDT:USDT" if "BTC" in ticker else ticker

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
