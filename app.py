from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # Parse incoming JSON payload from TradingView
        data = request.get_json(force=True)
        
        if not data:
            return jsonify({"status": "error", "message": "No JSON payload received"}), 400

        # Dynamically extract fields (Fixes the static lot size bug)
        ticker = data.get("alert_name", "BTCUSD.P")
        timeframe = data.get("timeframe", "1")
        action = data.get("action")          # "buy" or "sell"
        size = float(data.get("size", 1.0))  # Dynamically reads 1.0 or 2.0 from TradingView

        # Print the output to your Render Live Logs to verify correct parsing
        print(f"[DELTA] Signal Received -> Ticker: {ticker}, Timeframe: {timeframe}, Action: {action}, Target Lot Size: {size}")

        # ==========================================
        # TODO: Insert your Delta Exchange API call here
        # ==========================================
        # Example structure using your extracted variables:
        # response = client.create_order(
        #     product_id=ticker,
        #     size=size,
        #     side=action,
        #     order_type="market"
        # )

        return jsonify({"status": "success", "action": action, "size": size}), 200

    except Exception as e:
        print(f"[ERROR] Webhook processing failed: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
