import os
import requests
from flask import Flask, request, jsonify
from config import (
    MACHINE_MAP, LOCATION_ALIASES, DIAGNOSTIC_RULES, 
    BYPASS_BOT_KEYWORDS
)

app = Flask(__name__)

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mi_token_secreto_vending_123")

# Almacenamiento de estados de usuarios en memoria
USER_STATES = {}

def send_whatsapp_message(to_phone, text_message):
    """Envía mensaje de texto vía WhatsApp Cloud API."""
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
    """Detecta el código de máquina o alias en el texto enviado."""
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
    return "Servidor Bot SCR Vending SAS - Estado Estable Activo", 200

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
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
        print(f"Error procesando webhook: {e}")
    return jsonify({"status": "received"}), 200

def process_user_interaction(phone, text, msg_type):
    text_lower = text.lower()

    # 1. Palabras de desvío administrativo / Proveedores (Silenciado permanente)
    if any(keyword in text_lower for keyword in BYPASS_BOT_KEYWORDS):
        USER_STATES[phone] = {"state": "SILENCED_ADMIN", "data": {}}
        send_whatsapp_message(
            phone, 
            "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido redirigido con el equipo encargado."
        )
        return

    # Si el número está silenciado administrativamente, ignora
    user_session = USER_STATES.get(phone, {"state": "NEW", "data": {}})
    if user_session.get("state") == "SILENCED_ADMIN":
        return

    # Si ya completó la solicitud y está esperando reembolso
    if user_session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW"]:
        send_whatsapp_message(
            phone,
            "Hola de nuevo. Tu reporte sigue en proceso de gestión. "
            "Te recordamos que las devoluciones se realizan en el transcurso del día y te compartiremos la foto del comprobante por aquí. ¡Agradecemos tu paciencia! 🙏"
        )
        return

    # --- PASO 0: MENÚ INICIAL ---
    if user_session.get("state") == "NEW":
        if text == "1":
            USER_STATES[phone] = {"state": "AWAITING_MACHINE", "data": {}}
            send_whatsapp_message(
                phone,
                "Lamentamos el inconveniente. Vamos a ayudarte a gestionarlo paso a paso 🛠️\n\n"
                "1️⃣ Para empezar, indícanos el *código de la máquina* (el código sticker arriba a la derecha de la máquina, ej: A08, B001) "
                "o la *ubicación/conjunto* donde se encuentra. Si no lo encuentras, simplemente escríbenos el nombre del lugar."
            )
        elif text == "2":
            USER_STATES[phone] = {"state": "SILENCED_ADMIN", "data": {}}
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

    # --- PASO 1: MÁQUINA / UBICACIÓN ---
    elif user_session.get("state") == "AWAITING_MACHINE":
        detected_code = detect_machine_code(text)
        if detected_code:
            machine_name = MACHINE_MAP.get(detected_code, "Ubicación registrada")
            display_location = f"{detected_code} - {machine_name}"
        else:
            display_location = text if text else "Ubicación informada"

        USER_STATES[phone]["data"]["machine_name"] = display_location
        USER_STATES[phone]["state"] = "AWAITING_FAULT_DESCRIPTION"

        send_whatsapp_message(
            phone,
            f"Ubicación registrada: *{display_location}* 📍\n\n"
            "2️⃣ Cuéntanos explícitamente *¿qué le sucedió a la máquina?* (ej: producto atascado, no dio cambio, dinero retenido, cobro digital sin despacho).\n\n"
            "📌 *Nota: Atendemos únicamente novedades del día de hoy. No procesamos mensajes de audio.*"
        )

    # --- PASO 2: DESCRIPCIÓN DE LA NOVEDAD ---
    elif user_session.get("state") == "AWAITING_FAULT_DESCRIPTION":
        USER_STATES[phone]["data"]["claim_text"] = text
        USER_STATES[phone]["data"]["photo_attempts"] = 1
        USER_STATES[phone]["state"] = "AWAITING_PHOTO"

        send_whatsapp_message(
            phone,
            "Registro tomado 📝\n\n"
            "3️⃣ Indícanos la *hora aproximada* del inconveniente y adjunta una *foto de la pantalla o del producto*."
        )

    # --- PASO 3: HORA Y FOTO DE EVIDENCIA ---
    elif user_session.get("state") == "AWAITING_PHOTO":
        photo_attempts = USER_STATES[phone]["data"].get("photo_attempts", 1)

        # Si no envía imagen en el primer intento, pide la foto una vez más
        if msg_type not in ["image", "document"] and photo_attempts == 1:
            USER_STATES[phone]["data"]["photo_attempts"] = 2
            send_whatsapp_message(
                phone,
                "Para validar con precisión el estado del resorte o la pantalla, por favor *envíanos una foto clara* de la máquina o del producto."
            )
            return

        # Si envía foto O ya cumplió el 2º intento, avanza al paso del MONTO
        display_loc = USER_STATES[phone]["data"].get("machine_name", "la máquina")
        USER_STATES[phone]["state"] = "AWAITING_AMOUNT"

        send_whatsapp_message(
            phone,
            f"Entendido. Hemos registrado tu reporte para *{display_loc}* 🛠️\n\n"
            "Validando la información suministrada con la telemetría de la máquina...\n\n"
            "✅ *Novedad preaprobada*\n\n"
            "1️⃣ Indícanos el *monto exacto ($)* que te debemos *(sujeto a verificación)*."
        )

    # --- PASO 4: REGISTRO DE MONTO ---
    elif user_session.get("state") == "AWAITING_AMOUNT":
        USER_STATES[phone]["data"]["amount"] = text
        USER_STATES[phone]["state"] = "AWAITING_NEQUI"

        send_whatsapp_message(
            phone,
            "Monto registrado 💵\n\n"
            "2️⃣ Por favor indícanos tu *número de Nequi*.\n\n"
            "📌 *Importante: Solamente manejamos devolución por Nequi. Si no cuentas con una cuenta propia, por favor compártenos el número de Nequi de un familiar o persona conocida, ya que no disponemos de otro medio para realizar la devolución.*"
        )

    # --- PASO 5: REGISTRO DE NEQUI Y CIERRE ---
    elif user_session.get("state") == "AWAITING_NEQUI":
        USER_STATES[phone]["data"]["nequi"] = text
        USER_STATES[phone]["state"] = "WAITING_REFUND"

        send_whatsapp_message(
            phone,
            "✅ *¡Datos registrados exitosamente!*\n\n"
            "Tu solicitud ha quedado en cola de atención. Tan pronto nuestro equipo realice la transferencia por Nequi, te adjuntaremos el comprobante por este medio.\n\n"
            "¡Que tengas un feliz día!"
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
