from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import re
import uuid
from datetime import datetime
from pymongo import MongoClient

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
MONGO_URI = os.getenv("MONGO_URI")

# Conexión a MongoDB
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client["tamachi_database"]
        chats_collection = db.chats
        print("Conexión a MongoDB Atlas establecida.")
    except Exception as e:
        print(f"Error al conectar a MongoDB: {e}")
        chats_collection = None
else:
    chats_collection = None
    print("Advertencia: MONGO_URI no definida. Sin persistencia de datos.")

# --- RUTAS DE LAS PÁGINAS WEB ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/servicios")
def servicios():
    return render_template("servicios.html")

@app.route("/nosotros")
def nosotros():
    return render_template("nosotros.html")

@app.route("/ia")
def ia_interface():
    return render_template("chatbot.html")

# --- RUTAS DE LA API (TRIAGE IA) ---

@app.route("/api/chats", methods=["GET"])
def get_user_chats():
    """Devuelve la lista de chats de un usuario para la barra lateral."""
    user_id = session.get("user_id")
    if not user_id or chats_collection is None:
        return jsonify([])
    
    user_chats = list(chats_collection.find({"user_id": user_id}, {"_id": 0, "chat_id": 1, "title": 1}).sort("updated_at", -1))
    return jsonify(user_chats)

@app.route("/api/chat/<chat_id>", methods=["GET"])
def get_chat_history(chat_id):
    """Devuelve el historial de un chat específico."""
    if chats_collection is None:
        return jsonify([])
        
    chat_data = chats_collection.find_one({"chat_id": chat_id})
    if chat_data:
        return jsonify(chat_data.get("history", []))
    return jsonify([])

@app.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "API Key no configurada"}), 500

    data = request.json
    user_message = data.get("message")
    chat_id = data.get("chatId") # Puede venir vacío si es un nuevo triaje
    
    if not user_message:
        return jsonify({"error": "Mensaje vacío"}), 400

    # Gestión de Usuario
    user_id = session.get("user_id")
    if not user_id:
        user_id = os.urandom(16).hex()
        session["user_id"] = user_id

    # Gestión de Chat (Nuevo o Existente)
    conversation_history = []
    is_new_chat = False
    
    if chat_id and chats_collection is not None:
        chat_data = chats_collection.find_one({"chat_id": chat_id})
        if chat_data:
            conversation_history = chat_data.get("history", [])
    else:
        chat_id = str(uuid.uuid4())
        is_new_chat = True

    # Preparar el contexto para la IA
    max_history = 12
    recent_history = conversation_history[-max_history:] if len(conversation_history) > max_history else conversation_history
    context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
    
    # Instrucción de Sistema - Identidad Tamachi
    prompt_with_history = f"""Eres Tamachi-Diagnostic, el arquitecto de software y soporte técnico de Tamachi Studios S.L., Madrid.
    Reglas:
    - Ofreces Reparación Radical (optimización, SSD, térmica) antes de sugerir comprar hardware nuevo.
    - Eres experto en redes: BIND9, DHCP, Ubuntu Server y Apache2.
    - Conoces el programa de economía circular 'Segunda Vida'.
    - Tu tono es profesional, vanguardista, sobrio y muy directo. Eres resolutivo.
    
    Historial:
    {context_messages}
    Usuario: {user_message}
    Tamachi-Diagnostic:"""

    # Llamada a Gemini
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_with_history}]}]}
    
    try:
        response = requests.post(GEMINI_API_URL, headers=headers, params={"key": GEMINI_API_KEY}, json=payload)
        response.raise_for_status()
        response_json = response.json()

        if "candidates" in response_json:
            bot_response = response_json["candidates"][0]["content"]["parts"][0]["text"]
            
            # Actualizar historial
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "bot", "content": bot_response})
            
            # Guardar en BD
            if chats_collection is not None:
                title = user_message[:30] + "..." if is_new_chat else chat_data.get("title", "Sesión de Triaje")
                chats_collection.update_one(
                    {"chat_id": chat_id},
                    {"$set": {
                        "user_id": user_id,
                        "title": title,
                        "history": conversation_history,
                        "updated_at": datetime.utcnow()
                    }},
                    upsert=True
                )

            return jsonify({"response": bot_response, "chatId": chat_id, "title": title if is_new_chat else None})
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True)