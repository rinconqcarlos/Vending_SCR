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

# Inicialización del cliente de Gemini API
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Conexión a Redis en Upstash (Persistencia real)
redis_url = os.environ.get("UPSTASH_REDIS_REST_URL")
redis_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
r = Redis(url=redis_url, token=redis_token) if redis_url and redis_token else None

# Pool de hilos para procesamiento asíncrono
executor = ThreadPoolExecutor(max_workers=5)

# Tiempo de ventana activa: 36 horas en segundos
WINDOW_36_HOURS_SECONDS = 129600

# Palabras de cortesía/cierre que el bot debe ignorar al finalizar
GRATITUDE_KEYWORDS = ["gracias", "mil gracias", "ok", "listo", "dale", "perfecto", "bueno", "excelente", "muchas gracias"]

# System Instruction para el Orquestador Empático de SCR Vending
SYSTEM_INSTRUCTION = f"""
Eres el asistente virtual oficial y empático de atención al cliente de "SCR Vending SAS" en Colombia.
Tu objetivo principal es atender solicitudes de clientes que tuvieron inconvenientes con máquinas expendedoras de productos o dinero retenido.

REGLAS DE TONO Y EMPATÍA:
- Si el cliente reporta un problema, disculpate sinceramente por la molestia ocasionada a nombre de SCR Vending. Se amable, cercano y profesional.
- Atiendes mensajes de texto, fotos y audios transcritos.
- Habla en español de Colombia, claro y conciso.

OBJETIVO DE RECOLECCIÓN DE DATOS (CHECKLIST DE 5 REQUISITOS):
Debes verificar y recolectar gradualmente 5 datos obligatorios para procesar una devolución:
1. "machine": Código de máquina o ubicación/conjunto (Ubicaciones conocidas: {json.dumps(MACHINE_MAP, ensure_ascii=False)}).
2. "fault": Descripción del inconveniente (producto atascado, cobro sin despacho, etc.).
3. "has_photo": Confirmación de si envió foto o evidencia física de la máquina/pantalla/producto.
4. "amount": Monto exacto ($) a devolver.
5. "nequi": Número de cuenta Nequi para realizar la transferencia.

INSTRUCCIONES DE RESPUESTA:
- Analiza el mensaje actual del cliente (texto, foto o audio) junto con el estado actual del checklist suministrado en el contexto.
- Extrae de forma inteligente cualquier dato que el cliente haya proporcionado en el mensaje actual.
- Si falta algún dato del checklist, responde pidiendo disculpas por el inconveniente y solicita amablemente el dato o datos faltantes de forma conversacional y natural.
- Si faltan varios datos, no atosigues al cliente; pide el dato más lógico en orden (máquina/falla -> foto -> monto -> Nequi).
- NUNCA prometas transferencias inmediatas en minutos ni apruebes reembolsos de dinero de forma definitiva. Indica que el equipo revisará la novedad y enviará el comprobante por WhatsApp durante el día.

DEBES RETORNAR TU RESPUESTA ÚNICAMENTE EN FORMATO JSON CON ESTA ESTRUCTURA EXACTA:
{{
  "extracted_data": {{
    "machine": string o null,
    "fault": string o null,
    "has_photo": boolean,
    "amount": string o null,
    "nequi": string o null
  }},
  "reply_text": "Texto empático de respuesta para enviar al usuario por WhatsApp",
  "is_complete": boolean (true solo si los 5 datos están recolectados y verificados)
}}
"""

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
        return {"state": "COLLECTING", "checklist": {"machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None}, "last_interaction": 0}
    raw = r.get(f"session:{phone}")
    if raw:
        return json.loads(raw)
    return {"state": "COLLECTING", "checklist": {"machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None}, "last_interaction": 0}

def save_session(phone, session, ttl=WINDOW_36_HOURS_SECONDS):
    """Guarda la sesión actualizando la marca de tiempo."""
    session["last_interaction"] = time.time()
    if r:
        if session.get("state") == "SILENCED_ADMIN":
            ttl = 2592000  # 30 días para contactos administrativos
        r.set(f"session:{phone}", json.dumps(session), ex=ttl)

def is_duplicate_message(msg_id):
    """Deduplica mensajes por ID de WhatsApp."""
    if not r or not msg_id:
        return False
    return not r.set(f"msg:{msg_id}", "1", nx=True, ex=86400)

def send_whatsapp_message(to_phone, text_message):
    """Envía mensaje vía WhatsApp Cloud API."""
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
    """Descarga archivos multimedia (fotos, audios) enviados por el usuario."""
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

def call_gemini_orchestrator(incoming_text, media_bytes, mime_type, current_checklist):
    """Llama a Gemini API para analizar la interacción y orquestar el flujo empático."""
    if not ai_client:
        return None

    prompt_context = f"""
    ESTADO ACTUAL DEL CHECKLIST DEL CLIENTE EN SISTEMA:
    {json.dumps(current_checklist, ensure_ascii=False)}

    MENSAJE O DESCRIPCIÓN ENTRANTE DEL CLIENTE:
    "{incoming_text}"
    """

    contents = [prompt_context]

    if media_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=media_bytes, mime_type=mime_type))

    try:
        response = ai_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Error consultando Gemini API: {e}")
        return None

@app.route("/", methods=["GET"])
def home():
    return "Servidor Bot SCR Vending SAS - Orquestador Gemini Activo", 200

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
                        incoming_text = message_data.get("image", {}).get("caption", "Foto adjunta del usuario").strip()
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
    session = get_session(phone)
    current_time = time.time()
    last_interaction = session.get("last_interaction", 0)

    # REGLA DE LAS 36 HORAS: Si el caso ya fue completado hace menos de 36 horas, el bot permanece en silencio
    time_elapsed = current_time - last_interaction
    if session.get("state") == "COMPLETED" and time_elapsed < WINDOW_36_HOURS_SECONDS:
        save_session(phone, session)
        return

    # Si el cliente responde con palabras de cierre/agradecimiento
    if any(kw in text_lower for kw in GRATITUDE_KEYWORDS):
        if session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW", "COMPLETED"]:
            session["state"] = "COMPLETED"
            save_session(phone, session)
            return

    # Evaluación de palabras clave administrativas
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
            "Gracias por comunicarte con el área administrativa de SCR Vending. Tu mensaje ha sido redirigido con el equipo encargado."
        )
        return

    if session.get("state") == "SILENCED_ADMIN":
        return

    # Si ya se completó el checklist y está esperando transferencia
    if session.get("state") in ["WAITING_REFUND", "UNDER_REVIEW"]:
        save_session(phone, session)
        send_whatsapp_message(
            phone,
            "Hola de nuevo. Tu reporte ya se encuentra registrado y en cola de gestión. "
            "Tan pronto el equipo realice la transferencia por Nequi, te compartiremos la foto del comprobante por este medio. ¡Agradecemos tu paciencia! 🙏"
        )
        return

    # --- ORQUESTACIÓN CON GEMINI API ---
    current_checklist = session.get("checklist", {
        "machine": None, "fault": None, "has_photo": False, "amount": None, "nequi": None
    })

    # Si la solicitud trae foto, marcar la evidencia en el checklist
    if msg_type == "image":
        current_checklist["has_photo"] = True

    gemini_result = call_gemini_orchestrator(text, media_bytes, mime_type, current_checklist)

    if gemini_result:
        extracted = gemini_result.get("extracted_data", {})
        
        # Actualizar datos en el checklist persistente de Redis
        if extracted.get("machine"): current_checklist["machine"] = extracted["machine"]
        if extracted.get("fault"): current_checklist["fault"] = extracted["fault"]
        if extracted.get("has_photo"): current_checklist["has_photo"] = True
        if extracted.get("amount"): current_checklist["amount"] = extracted["amount"]
        if extracted.get("nequi"): current_checklist["nequi"] = extracted["nequi"]

        session["checklist"] = current_checklist

        # Verificar si todos los 5 datos requeridos ya fueron recolectados
        is_complete = gemini_result.get("is_complete", False) or all([
            current_checklist.get("machine"),
            current_checklist.get("fault"),
            current_checklist.get("has_photo"),
            current_checklist.get("amount"),
            current_checklist.get("nequi")
        ])

        if is_complete:
            session["state"] = "WAITING_REFUND"
            save_session(phone, session)
            send_whatsapp_message(
                phone,
                f"✅ *¡Datos registrados exitosamente!*\n\n"
                f"Hemos tomado nota de tu novedad para la ubicación *{current_checklist.get('machine')}* por un monto de *${current_checklist.get('amount')}* al Nequi *{current_checklist.get('nequi')}*.\n\n"
                "Tu solicitud ha quedado en cola de atención. Tan pronto nuestro equipo realice la transferencia, te compartiremos el comprobante por este medio. ¡Ofrecemos disculpas por el inconveniente y te deseamos un feliz día!"
            )
        else:
            session["state"] = "COLLECTING"
            save_session(phone, session)
            reply_text = gemini_result.get("reply_text", "Lamentamos lo sucedido. ¿Nos podrías brindar más detalles de la máquina o el Nequi para ayudarte?")
            send_whatsapp_message(phone, reply_text)
    else:
        # Fallback de seguridad si falla la API de Gemini
        save_session(phone, session)
        send_whatsapp_message(
            phone,
            "Hola, lamentamos los inconvenientes presentados con la máquina 🛠️. Por favor indícanos la ubicación, qué sucedió, adjunta una foto, el monto retenido y tu número de Nequi para procesar tu devolución."
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
