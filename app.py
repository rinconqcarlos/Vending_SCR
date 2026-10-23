import os
import re
import hmac
import hashlib
import json
import requests
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor
from upstash_redis import Redis
from config import (
    MACHINE_MAP, LOCATION_ALIASES, DIAGNOSTIC_RULES, 
    BYPASS_BOT_KEYWORDS
)

app = Flask(__name__)

# Configuración de variables de entorno
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mi_token_secreto_vending_123")
APP_SECRET = os.environ.get("APP_SECRET", "")

# Conexión a Redis en Upstash (Persistencia real)
redis_url = os.environ.get("UPSTASH_REDIS_REST_URL")
redis_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
r = Redis(url=redis_url, token=redis_token) if redis_url and redis_token else None

# Pool de hilos para procesamiento asíncrono
executor = ThreadPoolExecutor(max_workers=5)

def verify_signature(payload, signature):
    """Valida la firma X-Hub-Signature-256 para seguridad."""
    if not APP_SECRET or not signature:
        return True
    expected_sig = "sha256=" + hmac.new(
        APP_SECRET.encode('utf-8'), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def get_session(phone):
    """Obtiene la sesión del usuario desde Redis."""
    if not r:
        return {"state": "NEW", "data": {}}
    raw = r.get(f"session:{phone}")
    return json.loads(raw) if raw else {"state": "NEW", "data": {}}

def save_session(phone, session, ttl=172800):
    """Guarda la sesión en Redis con tiempo de vida (TTL por defecto: 48h)."""
    if r:
        if session.get("state") == "SILENCED_ADMIN":
            ttl = 2592000  # 30 días para silenciados administrativamente
        r.set(f"session:{phone}", json.dumps(session), ex=ttl)

def is_duplicate_message(msg_id):
    """Deduplica mensajes por ID de WhatsApp (wamid)."""
    if not r or not msg_id:
        return False
    return not r.set(f"msg:{msg_id}", "1", nx=True, ex=86400)

def send_whatsapp_message(to_phone, text_message):
    """Envía mensaje vía WhatsApp Cloud API con timeout configurado."""
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
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        print(f"Error enviando mensaje a {to_phone}: {e}")
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
    return "Servidor Bot SCR Vending SAS - Arquitectura Redis & Asíncrona Activa", 200

@app.route("/webhook", methods=["GET"])
def verify_webhook():
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def receive_message():
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(request.get_data(), signature):
        return "Firma no válida", 403

    data = request.get_json()

    # Procesamiento asíncrono en segundo plano e independientemente del return
    executor.submit(async_process_webhook, data)
    return jsonify({"status": "received"}), 200

def async_process_webhook(data):
    """Procesa las novedades de Meta en segundo plano."""
    try:
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                
                messages = value.get("messages", [])
                for message_data in messages:
                    msg_id = message_data.get("id")
                    
                    if is_duplicate_message(msg_id):
                        print(f"Mensaje duplicado omitido: {msg_id}")
                        continue

                    from_phone = message_data.get("from")
                    msg_type = message_data.get("type")
                    incoming_text = ""

                    if msg_type == "text":
                        incoming_text = message_data.get("text", {}).get("body", "").strip()
                    elif msg_type == "image":
                        incoming_text = message_data.get("image", {}).get("caption", "").strip()
                    elif msg_type == "document":
                        incoming_text = message_data.get("document", {}).get("caption", "").strip()

                    process_user_interaction(from_phone, incoming_text, msg_type)
    except Exception as e:
        print(f"Error procesando webhook asíncrono: {e}")

def process_user_interaction(phone, text, msg_type):
    text_lower = text.lower()
    session = get_session(phone)

    # Evaluación con expresiones regulares (palabras completas con \b)
    is_admin_keyword = False
    for kw in BYPASS_BOT_KEYWORDS:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text_lower):
            is_admin_keyword = True
            break

    if is_admin_keyword:
        session = {"state": "SILENCED_ADMIN", "data": {}}
        save_session(phone, session)
        send_whatsapp_message(
            phone, 
            "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido redirigido con el equipo encargado."
        )
        return

    if session.get("state") == "SILENCED_ADMIN":
        return

    if session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW"]:
        send_whatsapp_message(
            phone,
            "Hola de nuevo. Tu reporte sigue en proceso de gestión. "
            "Te recordamos que las devoluciones se realizan en el transcurso del día y te compartiremos la foto del comprobante por aquí. ¡Agradecemos tu paciencia! 🙏"
        )
        return

    # --- PASO 0: MENÚ INICIAL ---
    if session.get("state") == "NEW":
        if text == "1":
            session = {"state": "AWAITING_MACHINE", "data": {}}
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "Lamentamos el inconveniente. Vamos a ayudarte a gestionarlo paso a paso 🛠️\n\n"
                "1️⃣ Para empezar, indícanos el *código de la máquina* (el código sticker arriba a la derecha de la máquina, ej: A08, B001) "
                "o la *ubicación/conjunto* donde se encuentra. Si no lo encuentras, simplemente escríbenos el nombre del lugar."
            )
        elif text == "2":
            session = {"state": "SILENCED_ADMIN", "data": {}}
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido canalizado con el equipo encargado."
            )
        else:
            send_whatsapp_message(
                phone,
                "¡Hola! Bienvenido al canal de atención de *SCR Vending* 🤖\n\n"
                "Para ayudarte a procesar tu devolución o novedad, por favor selecciona una opción enviando únicamente el número:\n\n"
                "1️⃣ Tuve un problema con un producto o saldo en una máquina.\n"
                "2️⃣ Proveedores, facturación o gestión administrativa."
            )

    # --- PASO 1: MÁQUINA / UBICACIÓN ---
    elif session.get("state") == "AWAITING_MACHINE":
        detected_code = detect_machine_code(text)
        if detected_code:
            machine_name = MACHINE_MAP.get(detected_code, "Ubicación registrada")
            display_location = f"{detected_code} - {machine_name}"
        else:
            display_location = text if text else "Ubicación informada"

        session["data"]["machine_name"] = display_location
        session["state"] = "AWAITING_FAULT_DESCRIPTION"
        save_session(phone, session)

        send_whatsapp_message(
            phone,
            f"Ubicación registrada: *{display_location}* 📍\n\n"
            "2️⃣ Cuéntanos explícitamente *¿qué le sucedió a la máquina?* (ej: producto atascado, no dio cambio, dinero retenido, cobro digital sin despacho).\n\n"
            "📌 *Nota: Atendemos únicamente novedades del día de hoy. No procesamos mensajes de audio.*"
        )

    # --- PASO 2: NOVEDAD ---
    elif session.get("state") == "AWAITING_FAULT_DESCRIPTION":
        session["data"]["claim_text"] = text
        session["data"]["photo_attempts"] = 1
        session["state"] = "AWAITING_PHOTO"
        save_session(phone, session)

        send_whatsapp_message(
            phone,
            "Registro tomado 📝\n\n"
            "3️⃣ Indícanos la *hora aproximada* del inconveniente y adjunta una *foto de la pantalla o del producto*."
        )

    # --- PASO 3: FOTO ---
    elif session.get("state") == "AWAITING_PHOTO":
        photo_attempts = session["data"].get("photo_attempts", 1)

        if msg_type not in ["image", "document"] and photo_attempts == 1:
            session["data"]["photo_attempts"] = 2
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "Para validar con precisión el estado del resorte o la pantalla, por favor *envíanos una foto clara* de la máquina o del producto."
            )
            return

        display_loc = session["data"].get("machine_name", "la máquina")
        session["state"] = "AWAITING_AMOUNT"
        save_session(phone, session)

        send_whatsapp_message(
            phone,
            f"Entendido. Hemos registrado tu reporte para *{display_loc}* 🛠️\n\n"
            "Validando la información suministrada...\n\n"
            "✅ *Novedad registrada*\n\n"
            "1️⃣ Indícanos el *monto exacto ($)* que te debemos *(sujeto a verificación)*."
        )

    # --- PASO 4: MONTO ---
    elif session.get("state") == "AWAITING_AMOUNT":
        session["data"]["amount"] = text
        session["state"] = "AWAITING_NEQUI"
        save_session(phone, session)

        send_whatsapp_message(
            phone,
            "Monto registrado 💵\n\n"
            "2️⃣ Por favor indícanos tu *número de Nequi*.\n\n"
            "📌 *Importante: Solamente manejamos devolución por Nequi. Si no cuentas con una cuenta propia, por favor compártenos el número de Nequi de un familiar o persona conocida, ya que no disponemos de otro medio para realizar la devolución.*"
        )

    # --- PASO 5: NEQUI Y CIERRE ---
    elif session.get("state") == "AWAITING_NEQUI":
        session["data"]["nequi"] = text
        session["state"] = "WAITING_REFUND"
        save_session(phone, session)

        send_whatsapp_message(
            phone,
            "✅ *¡Datos registrados exitosamente!*\n\n"
            "Tu solicitud ha quedado en cola de atención. Tan pronto nuestro equipo realice la transferencia por Nequi, te adjuntaremos el comprobante por este medio.\n\n"
            "¡Que tengas un feliz día!"
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
