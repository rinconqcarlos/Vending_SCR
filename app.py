import os
import time
import requests
from flask import Flask, request, jsonify
from config import (
    MACHINE_MAP, LOCATION_ALIASES, DIAGNOSTIC_RULES, 
    BYPASS_BOT_KEYWORDS, FOLLOWUP_PATTERNS, EXPIRATION_TIME_SECONDS
)

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mi_token_secreto_vending_123")

USER_STATES = {}

def clean_expired_states():
    current_time = time.time()
    expired = [
        phone for phone, session in USER_STATES.items() 
        if current_time - session.get("last_active", 0) > EXPIRATION_TIME_SECONDS
    ]
    for phone in expired:
        del USER_STATES[phone]

def send_whatsapp_message(to_phone, text_message):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text_message}
    }
    try:
        res = requests.post(url, json=payload, headers=headers)
        return res.json()
    except Exception as e:
        print(f"Error al enviar mensaje a {to_phone}: {e}")
        return None

def detect_machine_code(text):
    text_lower = text.lower()
    for code in MACHINE_MAP.keys():
        if code.lower() in text_lower:
            return code
    for alias, code in LOCATION_ALIASES.items():
        if alias in text_lower:
            return code
    return None

@app.route("/", methods=["GET"])
def home():
    return "Servidor Bot SCR Vending SAS - Reglas de Negocio Activas", 200

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    clean_expired_states()
    data = request.get_json()
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        if "messages" in value:
            message_data = value["messages"][0]
            from_phone = message_data["from"]
            message_type = message_data["type"]
            incoming_text = ""
            if message_type == "text":
                incoming_text = message_data["text"]["body"].strip()
            elif message_type in ["image", "document"]:
                incoming_text = message_data.get("caption", "").strip()
            process_user_interaction(from_phone, incoming_text, message_type)
    except Exception as e:
        print(f"Error en webhook: {e}")
    return jsonify({"status": "received"}), 200

def process_user_interaction(phone, text, msg_type):
    current_time = time.time()
    text_lower = text.lower()

    if any(keyword in text_lower for keyword in BYPASS_BOT_KEYWORDS):
        USER_STATES[phone] = {"state": "SILENCED_ADMIN", "last_active": current_time, "data": {}}
        send_whatsapp_message(
            phone, 
            "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido redirigido con el equipo encargado."
        )
        return

    user_session = USER_STATES.get(phone, {"state": "NEW", "last_active": current_time, "data": {}})

    if user_session.get("state") == "SILENCED_ADMIN":
        return

    if user_session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW"]:
        send_whatsapp_message(
            phone,
            "Hola de nuevo. Tu reporte sigue en proceso de gestión. "
            "Te recordamos que las devoluciones se realizan en el transcurso del día y te compartiremos la foto del comprobante por aquí. ¡Agradecemos tu paciencia! 🙏"
        )
        USER_STATES[phone]["last_active"] = current_time
        return

    if user_session.get("state") == "NEW":
        if text == "1":
            USER_STATES[phone] = {"state": "AWAITING_CLAIM_DETAILS", "last_active": current_time, "data": {}}
            send_whatsapp_message(
                phone,
                "Lamentamos el inconveniente. Para entender qué le sucedió a la máquina y darte la asistencia adecuada, por favor cuéntanos en un mensaje:\n\n"
                "1️⃣ *¿Qué le sucedió a la máquina?* (ej. producto atascado, no dio cambio, billete retenido).\n"
                "2️⃣ *Código o ubicación de la máquina:* (ej: A08, Centro Cultural, U. Cundinamarca, Amazilia).\n"
                "3️⃣ *Hora aproximada* del inconveniente.\n"
                "4️⃣ *Foto de la pantalla o producto* (opcional, si puedes tomarla nos ayuda mucho).\n\n"
                "📌 *Nota: Atendemos únicamente novedades del día de hoy. No procesamos mensajes de audio.*"
            )
        elif text == "2":
            USER_STATES[phone] = {"state": "SILENCED_ADMIN", "last_active": current_time, "data": {}}
            send_whatsapp_message(
                phone,
                "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido canalizado con el equipo encargado."
            )
        else:
            send_whatsapp_message(
                phone,
                "¡Hola! Bienvenido al canal de atención de *SCR Vending* 🤖\n\n"
                "Para ayudarte lo más rápido posible, por favor selecciona una opción enviando el número:\n\n"
                "1️⃣ Tuve un problema con un producto o saldo en una máquina.\n"
                "2️⃣ Proveedores, facturación o gestión administrativa."
            )

    elif user_session.get("state") == "AWAITING_CLAIM_DETAILS":
        detected_code = detect_machine_code(text)
        if not detected_code:
            send_whatsapp_message(
                phone,
                "Gracias por la explicación. Para poder registrar el reporte técnico, por favor indícanos el *código de la máquina* (ej: A01, A08, B001) o el *nombre del lugar/edificio* donde se encuentra ubicada."
            )
            return

        detected_fault = "DESCONOCIDO"
        for fault_type, keywords in DIAGNOSTIC_RULES.items():
            if any(kw in text_lower for kw in keywords):
                detected_fault = fault_type
                break

        machine_name = MACHINE_MAP.get(detected_code, "Ubicación registrada")

        USER_STATES[phone]["data"]["machine_code"] = detected_code
        USER_STATES[phone]["data"]["machine_name"] = machine_name
        USER_STATES[phone]["data"]["fault_type"] = detected_fault
        USER_STATES[phone]["data"]["claim_text"] = text
        USER_STATES[phone]["state"] = "AWAITING_NEQUI"
        USER_STATES[phone]["last_active"] = current_time

        send_whatsapp_message(
            phone,
            f"Entendido. Hemos registrado tu reporte para la máquina *{detected_code} - {machine_name}* 🛠️\n\n"
            "Por favor compártenos tu *número de Nequi* (o el de un familiar/conocido) para procesar tu devolución.\n\n"
            "📌 *Ten en cuenta: La devolución se realizará en el transcurso del día y te enviaremos la foto del comprobante por este mismo chat tan pronto sea ejecutada. 😉*"
        )

    elif user_session.get("state") == "AWAITING_NEQUI":
        USER_STATES[phone]["data"]["nequi"] = text
        USER_STATES[phone]["state"] = "WAITING_REFUND"
        USER_STATES[phone]["last_active"] = current_time

        send_whatsapp_message(
            phone,
            "✅ *¡Datos registrados exitosamente!*\n\n"
            "Tu solicitud ha quedado en cola de atención. Tan pronto nuestro equipo realice la transferencia por Nequi, te adjuntaremos el comprobante por este medio.\n\n"
            "¡Que tengas un feliz día!"
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
