import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# Token de verificación que configuras en Meta
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "tu_token_de_verificacion")

# Endpoint GET: Validación inicial que hace Meta cuando guardas el Webhook
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode and token:
        if mode == 'subscribe' and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return challenge, 200
        else:
            return "Forbidden", 403
    return "Bad Request", 400

# Endpoint POST: Recepción de mensajes en tiempo real
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    if not data:
        return "NO_DATA", 400

    try:
        entry = data.get('entry', [])[0]
        changes = entry.get('changes', [])[0]
        value = changes.get('value', {})

        # FILTRO DE COEXISTENCIA:
        # Descarta actualizaciones de estado y 'echoes' (mensajes enviados por ti desde la app del celular)
        if 'statuses' in value:
            return "EVENT_RECEIVED", 200

        messages = value.get('messages', [])
        if messages:
            message = messages[0]
            # Si el mensaje proviene de un cliente
            if message.get('from'):
                remitente = message.get('from')
                texto = message.get('text', {}).get('body', '')
                print(f"Mensaje entrante de {remitente}: {texto}")

    except Exception as e:
        print(f"Error procesando el webhook: {e}")

    return "EVENT_RECEIVED", 200

if __name__ == '__main__':
    # Configuración para ejecución local / Render
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
