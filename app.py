import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Credenciales desde variables de entorno
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "mi_token_secreto_vending_123")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

# Catálogo oficial de máquinas SCR Vending SAS
MAQUINAS_SCR = {
    "1": {"nombre": "Edificio Central - Piso 1", "tipo": "Mixta (Snacks y Bebidas)"},
    "2": {"nombre": "Sede Administrativa - Recepción", "tipo": "Café / Bebidas Calientes"},
    "3": {"nombre": "Planta Operativa - Descanso", "tipo": "Snacks Refrigerados"},
    "4": {"nombre": "Centro Logístico - Parqueadero", "tipo": "Bebidas Frías"}
}

def enviar_mensaje_whatsapp(telefono_destino, texto_mensaje):
    """Envía un mensaje de texto plano mediante la API Cloud de WhatsApp."""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print("ERROR: Faltan credenciales de WhatsApp.")
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

    try:
        response = requests.post(url, json=payload, headers=headers)
        print(f"Respuesta Meta ({response.status_code}): {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error al enviar mensaje: {e}")
        return False

@app.route('/', methods=['GET'])
def home():
    return "Servidor Bot SCR Vending SAS - Activo", 200

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("WEBHOOK_VERIFIED")
        return challenge, 200
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
                    telefono = msg.get('from')
                    
                    if msg.get('type') == 'text':
                        texto = msg.get('text', {}).get('body', '').lower().strip()
                        print(f"Mensaje de {telefono}: {texto}")

                        # 1. Menú Principal
                        if texto in ["hola", "buenas", "inicio", "menu", "menú", "bot"]:
                            respuesta = (
                                "🥤 *SCR Vending SAS - Atención de Servicio*\n\n"
                                "¡Hola! Bienvenido al canal de soporte automático.\n"
                                "Por favor, selecciona una opción respondiendo con el número:\n\n"
                                "1️⃣ Reportar falla en máquina (monedas, billetes, producto atascado).\n"
                                "2️⃣ Problema con pago electrónico (QR / Nequi / Daviplata).\n"
                                "3️⃣ Ver listado de máquinas y ubicaciones.\n"
                                "4️⃣ Hablar con un asesor humano."
                            )

                        # 2. Reporte de Fallas Físicas
                        elif texto == "1" or "falla" in texto or "moneda" in texto or "producto" in texto:
                            respuesta = (
                                "🛠️ *Reporte de Falla Técnica*\n\n"
                                "Lamentamos los inconvenientes. Para procesar tu reembolso o revisión:\n\n"
                                "1. Indícanos el *Número o Ubicación de la máquina*.\n"
                                "2. Describe brevemente el fallo (ej. 'Tragó $2.000', 'No cayó selección B3').\n\n"
                                "Un técnico revisará el historial de la máquina de inmediato."
                            )

                        # 3. Pagos Electrónicos
                        elif texto == "2" or "pago" in texto or "nequi" in texto or "qr" in texto:
                            respuesta = (
                                "💳 *Soporte de Pagos Electrónicos*\n\n"
                                "Si realizaste una transferencia y la máquina no despachó:\n\n"
                                "• Por favor adjunta la *foto o captura del comprobante de pago* por este chat.\n"
                                "• Incluye el código o número de la máquina.\n\n"
                                "Validaremos la transacción con el banco para realizar la devolución."
                            )

                        # 4. Directorio de Máquinas
                        elif texto == "3" or "maquinas" in texto or "máquinas" in texto:
                            lista_m = "\n".join([f"• *Máquina {k}*: {v['nombre']} ({v['tipo']})" for k, v in MAQUINAS_SCR.items()])
                            respuesta = (
                                f"📍 *Ubicaciones SCR Vending SAS*\n\n{lista_m}\n\n"
                                "Si reportas un fallo en alguna de ellas, envía el número correspondiente."
                            )

                        # 5. Transferencia a Asesor
                        elif texto == "4" or "asesor" in texto or "humano" in texto or "contacto" in texto:
                            respuesta = (
                                "👨‍💼 *Atención Personalizada*\n\n"
                                "Un miembro del equipo de SCR Vending SAS responderá directamente en este chat.\n"
                                "Horario de atención: Lunes a Sábado de 8:00 AM a 6:00 PM."
                            )

                        # 6. Respuesta por defecto
                        else:
                            respuesta = (
                                "Gracias por escribir a *SCR Vending SAS*.\n\n"
                                "Hemos recibido tu mensaje. Si se trata de una falla, indícanos la ubicación o el número de máquina. "
                                "Si deseas ver las opciones del menú, escribe *Hola*."
                            )

                        # Enviar respuesta automática
                        enviar_mensaje_whatsapp(telefono, respuesta)

    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
