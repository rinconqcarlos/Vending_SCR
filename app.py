import os
from flask import Flask, request, jsonify

app = Flask(__name__)

VERIFY_TOKEN = "mi_token_secreto_vending_123"

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        return challenge, 200
    return 'Token invalido', 403

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.get_json()
    print("Mensaje recibido:", data)
    return jsonify({"status": "recibido"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
