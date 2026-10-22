# config.py - Base de Conocimiento y Reglas de SCR Vending Bot
import os

BASE_DIR = r"G:\Mi unidad\Marketing SCR"
EXPIRATION_TIME_SECONDS = 129600  # 36 Horas

MACHINE_MAP = {
    "A01": "Hato Grande",
    "A02": "Parques de la Estación",
    "A03": "Serralta",
    "A04": "Caminos 2",
    "A05": "El Rocío Bosque Nativo",
    "A06": "Amazilia Tocancipá",
    "A07": "Batará",
    "A08": "Centro Cultural IMCTC",
    "A09": "Altamorada",
    "A10": "Naranjo 3",
    "A11": "Hospital 1",
    "A12": "U. Cundinamarca",
    "B001": "Café",
    "B002": "Coworking",
    "B003": "Los Pinos",
    "B004": "Olivo",
    "B005": "Hospital 2",
    "B006": "Álamo"
}

LOCATION_ALIASES = {
    "hato grande": "A01", "hatogrande": "A01",
    "estacion": "A02", "estación": "A02",
    "serralta": "A03",
    "caminos 2": "A04", "caminos ii": "A04", "caminos": "A04",
    "rocio": "A05", "rocío": "A05", "el rocio": "A05",
    "amazilia": "A06",
    "batara": "A07", "batará": "A07",
    "centro cultural": "A08", "imctc": "A08", "cultura": "A08",
    "altamorada": "A09", "alta morada": "A09",
    "naranjo 3": "A10", "naranjo": "A10", "naranjos": "A10",
    "hospital 1": "A11", "hospital": "A11",
    "u cundinamarca": "A12", "udec": "A12", "universidad": "A12",
    "cafe": "B001", "café": "B001",
    "coworking": "B002", "spaces": "B002",
    "los pinos": "B003", "pinos": "B003",
    "olivo": "B004", "los olivos": "B004",
    "hospital 2": "B005",
    "alamo": "B006", "álamo": "B006"
}

DIAGNOSTIC_RULES = {
    "PRODUCT_TRAPPED": [
        "atascado", "atorado", "trancado", "colgado", "atrapado", "resorte", 
        "espiral", "no cayo", "no salio", "bloqueado", "no despacho", "no arojo"
    ],
    "MONEY_RETENTION_NO_CHANGE": [
        "se comio", "se trago", "se robo", "vueltas", "cambio", "no marca saldo", 
        "no cuenta", "volvio a cero", "no devuelve", "retuvo", "no lee", "moneda", "billete"
    ],
    "DIGITAL_PAYMENT_FAILURE": [
        "cobro digital", "descuento nequi", "pago tarjeta", "datafono", 
        "desconto y no entrego", "transferencia sin despacho", "daviplata"
    ],
    "POWER_OUTAGE_OFFLINE": [
        "sin luz", "bajon de luz", "se fue la luz", "apagada", "fuera de servicio", 
        "sin energia", "sin senal", "pitando", "desconfigurada"
    ]
}

BYPASS_BOT_KEYWORDS = [
    "paca", "pacas", "coca cola", "cifrut", "masato", "factura electronica", 
    "factura electrónica", "nit", "cedula", "cédula", "surtidor", "recorredor", 
    "pedido", "pedidos", "entrega", "entregas", "proveedor", "sst", 
    "autorizacion", "autorización", "placa", "comodato", "propuesta comercial", 
    "surtilider", "surtilíder", "don carlos", "carlos", "sonia", "doña sonia", "dona sonia"
]

FOLLOWUP_PATTERNS = [
    "no he recibido", "sin respuesta", "cuanto se demora", "que paso", 
    "qué pasó", "me confirman", "consignaron", "comprobante", "reembolso", 
    "devolucion", "devolución", "seguimiento", "ayer"
]
