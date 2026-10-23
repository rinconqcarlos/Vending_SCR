import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Configuración de variables de entorno
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_secreto_vending_123")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# Catálogo de soporte básico para SCR Vending SAS
RESPUESTAS_AUTOMATICAS = {
    "moneda": "Si la máquina se tragó una moneda o billete, por favor indícanos el número de máquina y la ubicación para procesar la devolución.",
    "producto": "Si el producto no cayó pero se realizó el cobro, indícanos la selección (ej. A1) y el nombre de la máquina.",
    "pago": "Para problemas con pago QR / Nequi / Daviplata, por favor envíanos el comprobante de transferencia por este medio.",
    "contacto": "Un asesor de SCR Vending SAS revisará tu mensaje a la brevedad. Horario de atención: Lunes a Sábado de 8:00 AM a 6:00 PM."
}

def enviar_mensaje_whatsapp(telefono_destino, texto_mensaje):
    """Envía un mensaje de texto a través de la Cloud API de WhatsApp."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("ERROR: Faltan credenciales de WhatsApp (WHATSAPP_TOKEN o PHONE_NUMBER_ID).")
        return False

    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono_destino,
        "type": "text",
        "text": {"preview_url": False, "body": texto_mensaje}
    }

    response = requests.post(url, json=payload, headers=headers)
    print(f"Respuesta envío WhatsApp: {response.status_code} - {response.text}")
    return response.status_code == 200

@app.route('/', methods=['GET'])
def home():
    return "Servidor Bot SCR Vending SAS - Activo", 200

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("WEBHOOK_VERIFIED EN SERVIDOR")
        return challenge, 200
    else:
        return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()

    if data.get('object') == 'whatsapp_business_account':
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                messages = value.get('messages', [])

                if messages:
                    msg = messages[0]
                    telefono_remitente = msg.get('from')
                    
                    if msg.get('type') == 'text':
                        texto_recibido = msg.get('text', {}).get('body', '').lower()
                        print(f"Mensaje entrante de {telefono_remitente}: {texto_recibido}")

                        # Lógica de respuesta automática según palabras clave
                        if "hola" in texto_recibido or "inicio" in texto_recibido or "menu" in texto_recibido:
                            respuesta = (
                                "🤖 *SCR Vending SAS - Soporte Automatizado*\n\n"
                                "¡Hola! ¿En qué podemos ayudarte hoy?\n\n"
                                "1️⃣ Escribe *moneda* si tuviste problemas con monedas/billetes.\n"
                                "2️⃣ Escribe *producto* si no cayó tu selección.\n"
                                "3️⃣ Escribe *pago* si tuviste inconvenientes con pago electrónico.\n"
                                "4️⃣ Escribe *contacto* para hablar con un agente humano."
                            )
                        elif "moneda" in texto_recibido or "billete" in texto_recibido or "1" == texto_recibido.strip():
                            respuesta = RESPUESTAS_AUTOMATICAS["moneda"]
                        elif "producto" in texto_recibido or "2" == texto_recibido.strip():
                            respuesta = RESPUESTAS_AUTOMATICAS["producto"]
                        elif "pago" in texto_recibido or "qr" in texto_recibido or "3" == texto_recibido.strip():
                            respuesta = RESPUESTAS_AUTOMATICAS["pago"]
                        elif "contacto" in texto_recibido or "agente" in texto_recibido or "4" == texto_recibido.strip():
                            respuesta = RESPUESTAS_AUTOMATICAS["contacto"]
                        else:
                            respuesta = (
                                "Hemos recibido tu mensaje. Si reportas una falla en alguna máquina expendedora, "
                                "por favor indícanos la ubicación o el número de máquina. Un asesor te responderá a la brevedad."
                            )

                        # Enviar la respuesta por WhatsApp
                        enviar_mensaje_whatsapp(telefono_remitente, respuesta)

    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
