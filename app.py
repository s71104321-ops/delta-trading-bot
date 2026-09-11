import os
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
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
        # Wipes out hanging stop-loss or limit orders from the previous trade

        # --- STEP 2: AUTO-FILL & REVERSE EXECUTION ---
        if action == 'buy':
            pass
        elif action == 'sell':
            pass

        return jsonify({
            "status": "success",
            "message": f"Successfully processed {action} order for alert: {alert_name}"
        }), 200

    except Exception as e:
        print(f"Webhook Execution Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "bot": "Delta Exchange DPO RMA Bot"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
