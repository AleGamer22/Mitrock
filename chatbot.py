from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import re
from pymongo import MongoClient  # <-- descomentado/corregido

# NUEVAS IMPORTS:
import logging
import bleach
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)  # si se despliega detrás de proxy
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(32))

# Seguridad / límites
app.config.update({
    "MAX_CONTENT_LENGTH": 64 * 1024,  # 64 KB máximo por petición (ajusta según necesidad)
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "SESSION_COOKIE_SECURE": os.getenv("SESSION_COOKIE_SECURE", "1") == "1"
})

# CORS (solo si lo necesitas; restringe en producción)
CORS(app, resources={r"/api/*": {"origins": os.getenv("CORS_ORIGINS", "*")}})

# Rate limiting
limiter = Limiter(app, key_func=get_remote_address, default_limits=["60 per minute", "2000 per day"])

# Logging básico
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("mitrock")

# Seguridad: cabeceras HTTP recomendadas
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
    # CSP mínimo — adapta según recursos que necesites cargar
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https:;"
    # HSTS en producción
    if os.getenv("FLASK_ENV") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response

# Input limits
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "3000"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "chatbot_history"

if not GEMINI_API_KEY:
    logger.warning("La variable de entorno GEMINI_API_KEY no está definida.")

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client[DB_NAME]
        conversations = db.conversations
        logger.info("Conexión a MongoDB Atlas establecida.")
    except Exception as e:
        logger.exception("Error al conectar a MongoDB. Desactivando persistencia.")
        MONGO_URI = None
else:
    logger.info("MONGO_URI no definida; memoria persistente desactivada.")

def get_conversation_history(user_id, use_persistence=True):
    if use_persistence and MONGO_URI:
        conversation_data = conversations.find_one({"user_id": user_id})
        if conversation_data and "history" in conversation_data:
            return conversation_data["history"]
        else:
            return []
    else:
        return session.get("conversation_history", [])

def store_conversation_history(user_id, history, use_persistence=True):
    if use_persistence and MONGO_URI:
        conversations.update_one(
            {"user_id": user_id},
            {"$set": {"history": history}},
            upsert=True
        )
    else:
        session["conversation_history"] = history
        session.modified = True

@app.errorhandler(RequestEntityTooLarge)
def handle_large_request(e):
    return jsonify({"error": "Payload demasiado grande"}), 413

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/")
def index():
    return render_template("chatbot.html")

@app.route("/api/chat", methods=["POST"])
@limiter.limit("20/minute")  # ejemplo por IP
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "La API Key no está configurada en el servidor."}), 500

    if not request.is_json:
        return jsonify({"error": "Se esperaba JSON"}), 400

    user_message = request.json.get("message", "")
    if not isinstance(user_message, str) or not user_message.strip():
        return jsonify({"error": "Mensaje del usuario no proporcionado"}), 400

    # Validación de longitud
    if len(user_message) > MAX_INPUT_CHARS:
        return jsonify({"error": f"Mensaje demasiado largo (máx {MAX_INPUT_CHARS} caracteres)."}), 413

    # Saneamiento básico de entrada
    user_message = bleach.clean(user_message, tags=[], attributes={}, strip=True).strip()
    logger.info("Incoming message (len=%d) from %s", len(user_message), request.remote_addr)

    use_persistence = MONGO_URI is not None
    user_id = session.get("user_id")
    if not user_id:
        user_id = os.urandom(16).hex()
        session["user_id"] = user_id

    conversation_history = get_conversation_history(user_id, use_persistence) or []

    # limitar historial guardado
    max_history_length = int(os.getenv("MAX_HISTORY", "10"))
    if len(conversation_history) > max_history_length:
        conversation_history = conversation_history[-max_history_length:]

    # Normalizar roles: usar 'user' y 'bot'
    context_messages = "\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in conversation_history])
    prompt_with_history = (
        "Eres Mitrock, un asistente de IA amigable y útil. Responde en el idioma del usuario.\n"
        f"{context_messages}\nUsuario: {user_message}\nMitrock:"
    )

    headers = {"Content-Type": "application/json"}
    params = {"key": GEMINI_API_KEY}
    data = {
        "contents": [
            {"parts": [{"text": prompt_with_history}]}
        ]
    }

    try:
        response = requests.post(GEMINI_API_URL, headers=headers, params=params, json=data, timeout=30)
        response.raise_for_status()
        response_json = response.json()

        if "candidates" in response_json and response_json["candidates"]:
            bot_response_text = response_json["candidates"][0]["content"]["parts"][0]["text"]

            # convertir markdown simple a HTML (ejemplo con negritas) y sanitizar antes de devolver
            formatted_response = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", bot_response_text)
            safe_response_html = bleach.clean(formatted_response, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)

            # Guardar en historial con roles normalizados
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "bot", "content": safe_response_html})
            store_conversation_history(user_id, conversation_history, use_persistence)

            # Devolver HTML seguro y texto plano opcional
            return jsonify({"response": safe_response_html, "text": bot_response_text})
        else:
            logger.error("Modelo devolvió formato inesperado: %s", response_json)
            return jsonify({"error": "Respuesta del modelo vacía o con formato inesperado."}), 500

    except requests.exceptions.RequestException as e:
        logger.exception("Error al comunicarse con Gemini")
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {str(e)}"}), 500
    except Exception as e:
        logger.exception("Error inesperado en /api/chat")
        return jsonify({"error": f"Error inesperado en el servidor: {str(e)}"}), 500

@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    use_persistence = MONGO_URI is not None
    user_id = session.get("user_id")

    if use_persistence and MONGO_URI and user_id:
        conversations.update_one({"user_id": user_id}, {"$set": {"history": []}})
    else:
        session.pop("conversation_history", None)

    return jsonify({"message": "Chat history cleared"})

@app.route("/api/regenerate", methods=["POST"])
def regenerate():
    if not GEMINI_API_KEY:
        return jsonify({"error": "La API Key no está configurada en el servidor."}), 500

    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "Mensaje del usuario no proporcionado"}), 400

    use_persistence = MONGO_URI is not None
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "No hay un ID de usuario en la sesión."}), 400

    conversation_history = get_conversation_history(user_id, use_persistence)

    if not conversation_history:
        return jsonify({"error": "No hay historial de conversación para regenerar."}), 400

    if conversation_history[-1]["role"] != "bot":
        return jsonify({"error": "La última interacción no fue del bot."}), 400

    # Remove the last bot message
    conversation_history.pop()

    context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history])
    prompt_with_history = f"{context_messages}\nUsuario: {user_message}\nBot:"

    headers = {
        "Content-Type": "application/json",
    }
    params = {
        "key": GEMINI_API_KEY
    }
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_with_history}
                ]
            }
        ]
    }
    try:
        response = requests.post(GEMINI_API_URL, headers=headers, params=params, json=data)
        response.raise_for_status()
        response_json = response.json()

        if "candidates" in response_json and response_json["candidates"]:
            bot_response_text = response_json["candidates"][0]["content"]["parts"][0]["text"]
            formatted_response = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", bot_response_text)

            conversation_history.append({"role": "bot", "content": formatted_response})
            store_conversation_history(user_id, conversation_history, use_persistence)

            return jsonify({"response": formatted_response})
        else:
            return jsonify({"error": "Respuesta del modelo vacía o con formato inesperado."}), 500

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error inesperado en el servidor: {e}"}), 500

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    logger.info("Starting app on %s:%s debug=%s", host, port, debug)
    app.run(host=host, port=port, debug=debug)
