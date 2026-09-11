from flask import Flask, request, jsonify
import ccxt
import os

app = Flask(__name__)

# Initialize Delta Exchange connection using environment variables
exchange = ccxt.delta({
    'apiKey': os.getenv('DELTA_API_KEY'),
    'secret': os.getenv('DELTA_SECRET_KEY'),
    'enableRateLimit': True,
})

WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', 'my_secret_123')
FIXED_LOT_SIZE = 1  # Forces exact 1-lot order execution

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        
        # Security check
        if not data or data.get('secret') != WEBHOOK_SECRET:
            return jsonify({"status": "error", "message": "Unauthorized"}), 403
            
        action = data.get('action') # 'buy' or 'sell'
        ticker = data.get('ticker', 'BTCUSDT')
        
        print(f"Signal received: {action} for {ticker}")

        # Execute 1-lot market order (Delta handles position reversals natively)
        if action == 'buy':
            order = exchange.create_market_order(
                symbol='BTC/USDT:USDT', 
                type='market', 
                side='buy', 
                amount=FIXED_LOT_SIZE
            )
            print(f"1-Lot LONG Executed: {order['id']}")
            
        elif action == 'sell':
            order = exchange.create_market_order(
                symbol='BTC/USDT:USDT', 
                type='market', 
                side='sell', 
                amount=FIXED_LOT_SIZE
            )
            print(f"1-Lot SHORT Executed: {order['id']}")
            
        else:
            return jsonify({"status": "ignored", "message": "Invalid action"}), 400

        return jsonify({"status": "success", "order_id": order['id']}), 200

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
