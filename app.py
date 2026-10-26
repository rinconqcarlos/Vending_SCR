import os
import re
import hmac
import hashlib
import json
import time
import requests
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor
from upstash_redis import Redis
from google import genai
from google.genai import types
from config import MACHINE_MAP, LOCATION_ALIASES, BYPASS_BOT_KEYWORDS

app = Flask(__name__)

# Configuración de variables de entorno
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "mi_token_secreto_vending_123")
APP_SECRET = os.environ.get("APP_SECRET", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Cliente Gemini
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Conexión a Redis en Upstash (Persistencia)
redis_url = os.environ.get("UPSTASH_REDIS_REST_URL")
redis_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
r = Redis(url=redis_url, token=redis_token) if redis_url and redis_token else None

executor = ThreadPoolExecutor(max_workers=5)
WINDOW_36_HOURS_SECONDS = 129600
GRATITUDE_KEYWORDS = ["gracias", "mil gracias", "ok", "listo", "dale", "perfecto", "bueno", "excelente", "muchas gracias"]

SYSTEM_INSTRUCTION = f"""
Eres el asistente virtual de "SCR Vending SAS" en Colombia. Tu único trabajo es analizar el mensaje del usuario e interpretar qué dato del checklist está entregando o qué respuesta empática dar.

CHECKLIST DE CAMPOS:
- machine: Código o ubicación de la máquina ({json.dumps(MACHINE_MAP, ensure_ascii=False)})
- fault: Descripción del problema/falla
- has_photo: boolean (si el usuario dice que envió o adjuntó foto/evidencia)
- amount: Monto (\$) a devolver
- nequi: Número de Nequi

ESTRUCTURA DE RESPUESTA REQUERIDA (JSON):
Retorna EXCLUSIVAMENTE un objeto JSON válido:
{{
  "extracted_data": {{
    "machine": string o null,
    "fault": string o null,
    "has_photo": boolean,
    "amount": string o null,
    "nequi": string o null
  }},
  "reply_text": "Respuesta breve, empática y con emojis pidiendo el SIGUIENTE dato faltante del checklist"
}}
"""

def verify_signature(payload, signature):
    if not APP_SECRET or not signature:
        return True
    expected_sig = "sha256=" + hmac.new(
        APP_SECRET.encode('utf-8'), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected_sig)

def get_session(phone):
    if not r:
        return {"state": "NEW", "checklist": {"machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None}, "last_interaction": 0}
    raw = r.get(f"session:{phone}")
    if raw:
        return json.loads(raw)
    return {"state": "NEW", "checklist": {"machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None}, "last_interaction": 0}

def save_session(phone, session, ttl=WINDOW_36_HOURS_SECONDS):
    session["last_interaction"] = time.time()
    if r:
        if session.get("state") == "SILENCED_ADMIN":
            ttl = 2592000
        r.set(f"session:{phone}", json.dumps(session), ex=ttl)

def reset_session(phone):
    if r:
        r.delete(f"session:{phone}")

def is_duplicate_message(msg_id):
    if not r or not msg_id:
        return False
    return not r.set(f"msg:{msg_id}", "1", nx=True, ex=86400)

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
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        print(f"Error enviando mensaje a {to_phone}: {e}")
        return None

def download_media_file(media_id):
    try:
        url_media = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        res_info = requests.get(url_media, headers=headers, timeout=10)
        media_url = res_info.json().get("url")
        if not media_url:
            return None, None
        
        res_file = requests.get(media_url, headers=headers, timeout=15)
        mime_type = res_file.headers.get("Content-Type", "")
        return res_file.content, mime_type
    except Exception as e:
        print(f"Error descargando media {media_id}: {e}")
        return None, None

def call_gemini_fallback(incoming_text, media_bytes, mime_type, current_checklist):
    """Se activa únicamente cuando el mensaje es atípico o requiere interpretación."""
    if not ai_client:
        return None

    prompt_context = f"""
    CHECKLIST ACTUAL: {json.dumps(current_checklist, ensure_ascii=False)}
    MENSAJE ENTRANTE: "{incoming_text}"
    """
    contents = [prompt_context]
    if media_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))

    # Usamos gemini-1.5-flash nativo
    for model_id in ["gemini-1.5-flash", "gemini-2.0-flash"]:
        try:
            response = ai_client.models.generate_content(
                model=model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            raw_text = response.text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
            return json.loads(raw_text)
        except Exception as e:
            print(f"Intento Gemini con {model_id} falló: {e}")
    return None

@app.route("/", methods=["GET"])
def home():
    return "Servidor Bot SCR Vending SAS - Híbrido Rápido Activo", 200

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
    executor.submit(async_process_webhook, data)
    return jsonify({"status": "received"}), 200

def async_process_webhook(data):
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
                        continue

                    from_phone = message_data.get("from")
                    msg_type = message_data.get("type")
                    incoming_text = ""
                    media_bytes = None
                    mime_type = None

                    if msg_type == "text":
                        incoming_text = message_data.get("text", {}).get("body", "").strip()
                    elif msg_type == "image":
                        incoming_text = message_data.get("image", {}).get("caption", "Foto adjunta").strip()
                        media_id = message_data.get("image", {}).get("id")
                        media_bytes, mime_type = download_media_file(media_id)
                    elif msg_type == "audio":
                        incoming_text = "Nota de voz enviada por el usuario"
                        media_id = message_data.get("audio", {}).get("id")
                        media_bytes, mime_type = download_media_file(media_id)
                    elif msg_type == "document":
                        incoming_text = message_data.get("document", {}).get("caption", "Documento adjunto").strip()

                    process_user_interaction(from_phone, incoming_text, msg_type, media_bytes, mime_type)
    except Exception as e:
        print(f"Error procesando webhook: {e}")

def process_user_interaction(phone, text, msg_type, media_bytes=None, mime_type=None):
    text_lower = text.lower()

    # COMANDO RESET PARA PRUEBAS
    if text_lower == "reset":
        reset_session(phone)
        send_whatsapp_message(phone, "🔄 *Sesión reiniciada con éxito.* Envía cualquier mensaje para iniciar de nuevo.")
        return

    session = get_session(phone)
    current_time = time.time()
    last_interaction = session.get("last_interaction", 0)

    # REGLA DE 36 HORAS
    time_elapsed = current_time - last_interaction
    if session.get("state") == "COMPLETED" and time_elapsed < WINDOW_36_HOURS_SECONDS:
        save_session(phone, session)
        return

    # AGRADECIMIENTOS
    if any(kw in text_lower for kw in GRATITUDE_KEYWORDS):
        if session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW", "COMPLETED"]:
            session["state"] = "COMPLETED"
            save_session(phone, session)
            return

    # PALABRAS CLAVE ADMINISTRATIVAS
    is_admin_keyword = False
    for kw in BYPASS_BOT_KEYWORDS:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text_lower):
            is_admin_keyword = True
            break

    if is_admin_keyword:
        session = {"state": "SILENCED_ADMIN", "checklist": {}, "last_interaction": current_time}
        save_session(phone, session)
        send_whatsapp_message(
            phone, 
            "🤝 Gracias por comunicarte con el área administrativa de *SCR Vending*. Tu mensaje ha sido redirigido con el equipo encargado."
        )
        return

    if session.get("state") == "SILENCED_ADMIN":
        return

    if session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW"]:
        save_session(phone, session)
        send_whatsapp_message(
            phone,
            "👋 ¡Hola de nuevo! Tu reporte ya se encuentra registrado y en cola de gestión. "
            "Te compartiremos el comprobante por este medio en cuanto realicemos la transferencia a tu Nequi. ¡Agradecemos mucho tu paciencia! 🙏"
        )
        return

    # --- PASO 0: FILTRO INICIAL DETERMINISTA (MENÚ 1 O 2) ---
    if session.get("state") == "NEW":
        if text == "1":
            session["state"] = "COLLECTING"
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "Lamentamos mucho el inconveniente presentado con la máquina 🛠️. Vamos a ayudarte a gestionarlo paso a paso.\n\n"
                "1️⃣ Para empezar, indícanos el *código de la máquina* (el sticker arriba a la derecha, ej: A08, B001) "
                "o la *ubicación/conjunto* donde te encuentras. 📍"
            )
            return
        elif text == "2":
            session = {"state": "SILENCED_ADMIN", "checklist": {}, "last_interaction": current_time}
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "🤝 Gracias por comunicarte con el área administrativa de *SCR Vending*. Tu mensaje ha sido redirigido con el equipo encargado."
            )
            return
        else:
            if msg_type == "image":
                session["checklist"]["has_photo"] = True

            save_session(phone, session)
            send_whatsapp_message(
                phone,
                "¡Hola! 👋 Bienvenido al canal de atención de *SCR Vending* 🤖\n\n"
                "Para ayudarte de la mejor manera, por favor selecciona una opción enviando únicamente el número:\n\n"
                "1️⃣ Tuve un problema con un producto o saldo en una máquina. 🛠️\n"
                "2️⃣ Proveedores, facturación o gestión administrativa. 💼"
            )
            return

    # --- PASO 1 EN ADELANTE: FLUJO DETERMINISTA RÁPIDO CON CAÍDA A GEMINI ---
    checklist = session.get("checklist", {
        "machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None
    })

    if msg_type == "image":
        checklist["has_photo"] = True

    # 1. Intento por lógica determinista rápida (Respuesta instantánea)
    if not checklist.get("machine") and text and text != "1":
        checklist["machine"] = text
        session["checklist"] = checklist
        session["state"] = "COLLECTING"
        save_session(phone, session)
        send_whatsapp_message(phone, "Ubicación registrada 📍. Cuéntanos por favor, ¿qué le sucedió a la máquina o qué falla presentó? 📝")
        return

    elif checklist.get("machine") and not checklist.get("fault") and text:
        checklist["fault"] = text
        session["checklist"] = checklist
        session["state"] = "COLLECTING"
        save_session(phone, session)
        send_whatsapp_message(phone, "Entendido 📝. Si tienes una *foto de la pantalla o del producto atascado*, por favor adjúntala 📸 (si no tienes, escribe 'no tengo').")
        return

    elif checklist.get("machine") and checklist.get("fault") and not checklist.get("has_photo"):
        if msg_type == "image" or "no" in text_lower:
            if msg_type == "image":
                checklist["has_photo"] = True
            session["checklist"] = checklist
            session["state"] = "COLLECTING"
            save_session(phone, session)
            send_whatsapp_message(phone, "Foto/Evidencia anotada 📸. Indícanos el *monto exacto ($)* a devolver 💵.")
            return

    elif checklist.get("machine") and checklist.get("fault") and not checklist.get("amount") and text:
        # Extrae números del texto si están presentes
        amount_match = re.search(r'\b\d[\d\.\,]*\b', text)
        if amount_match:
            checklist["amount"] = amount_match.group(0)
            session["checklist"] = checklist
            session["state"] = "COLLECTING"
            save_session(phone, session)
            send_whatsapp_message(phone, "Monto anotado 💵. Por último, compártenos tu *número de Nequi* para gestionar la devolución 📲.")
            return

    elif checklist.get("machine") and checklist.get("fault") and checklist.get("amount") and not checklist.get("nequi") and text:
        nequi_match = re.search(r'\b3\d{9}\b', text)
        if nequi_match or len(text.strip()) >= 10:
            checklist["nequi"] = nequi_match.group(0) if nequi_match else text.strip()
            session["checklist"] = checklist
            session["state"] = "WAITING_REFUND"
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                f"✅ *¡Datos registrados exitosamente!*\n\n"
                f"Hemos tomado nota de tu reporte para la máquina en *{checklist.get('machine')}* por un monto de *${checklist.get('amount')}* al Nequi *{checklist.get('nequi')}*.\n\n"
                "📲 Tu solicitud quedó en cola de atención. Tan pronto realicemos la transferencia, te compartiremos el comprobante por este medio. ¡Ofrecemos disculpas por el inconveniente y te deseamos un feliz día! 🙏"
            )
            return

    # 2. Si el mensaje es complejo, audio o salta la lógica lineal -> Gemini interpreta el mensaje
    gemini_result = call_gemini_fallback(text, media_bytes, mime_type, checklist)

    if gemini_result and isinstance(gemini_result, dict):
        extracted = gemini_result.get("extracted_data", {})
        if extracted.get("machine"): checklist["machine"] = extracted["machine"]
        if extracted.get("fault"): checklist["fault"] = extracted["fault"]
        if extracted.get("has_photo"): checklist["has_photo"] = True
        if extracted.get("amount"): checklist["amount"] = extracted["amount"]
        if extracted.get("nequi"): checklist["nequi"] = extracted["nequi"]

        session["checklist"] = checklist

        if all([checklist.get("machine"), checklist.get("fault"), checklist.get("amount"), checklist.get("nequi")]):
            session["state"] = "WAITING_REFUND"
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                f"✅ *¡Datos registrados exitosamente!*\n\n"
                f"Hemos tomado nota de tu reporte para la máquina en *{checklist.get('machine')}* por un monto de *${checklist.get('amount')}* al Nequi *{checklist.get('nequi')}*.\n\n"
                "📲 Tu solicitud quedó en cola de atención. Tan pronto realicemos la transferencia, te compartiremos el comprobante por este medio. ¡Ofrecemos disculpas por el inconveniente y te deseamos un feliz día! 🙏"
            )
            return
        else:
            session["state"] = "COLLECTING"
            save_session(phone, session)
            reply_text = gemini_result.get("reply_text")
            if reply_text:
                send_whatsapp_message(phone, reply_text)
                return

    # 3. Fallback guiado paso a paso por defecto
    save_session(phone, session)
    if not checklist.get("machine"):
        msg = "Lamentamos los inconvenientes con la máquina 🛠️. Para colaborarte, ¿nos podrías indicar el *código de la máquina* o la *ubicación/conjunto*? 📍"
    elif not checklist.get("fault"):
        msg = "Ubicación registrada 📍. Cuéntanos por favor, ¿qué le sucedió a la máquina o qué falla presentó? 📝"
    elif not checklist.get("has_photo"):
        msg = "Entendido 📝. Si tienes una *foto de la pantalla o del producto atascado*, por favor adjúntala 📸 (si no tienes, escribe 'no tengo')."
    elif not checklist.get("amount"):
        msg = "Indícanos por favor el *monto exacto ($)* retenido a devolver 💵."
    else:
        msg = "Por favor compártenos tu *número de Nequi* para gestionar la devolución 📲."

    send_whatsapp_message(phone, msg)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
