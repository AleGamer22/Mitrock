from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import uuid
from datetime import datetime
from pymongo import MongoClient
import traceback

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "tamachi_clave_secreta_super_segura_2026")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
MONGO_URI = "mongodb+srv://tamachi_admin:N0JX7x7O1okRMDzF@tamachi.jje3ylk.mongodb.net/?appName=Tamachi"

chats_collection = None
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        client.server_info() 
        db = client["tamachi_database"]
        chats_collection = db.chats
        print("✅ Conexión a MongoDB Atlas establecida.")
    except Exception as e:
        print(f"❌ Error al conectar a MongoDB: {e}")
        chats_collection = None

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
    user_id = session.get("user_id")
    if not user_id or chats_collection is None:
        return jsonify([])
    try:
        user_chats = list(chats_collection.find({"user_id": user_id}, {"_id": 0, "chat_id": 1, "title": 1}).sort("updated_at", -1))
        return jsonify(user_chats)
    except Exception as e:
        return jsonify([])

@app.route("/api/chat/<chat_id>", methods=["GET"])
def get_chat_history(chat_id):
    if chats_collection is None:
        return jsonify([])
    try:
        chat_data = chats_collection.find_one({"chat_id": chat_id})
        if chat_data:
            return jsonify(chat_data.get("history", []))
    except Exception as e:
        pass
    return jsonify([])

# NUEVO: Ruta para borrar un chat
@app.route("/api/chat/<chat_id>", methods=["DELETE"])
def delete_chat(chat_id):
    if chats_collection is None:
        return jsonify({"error": "No database"}), 500
    user_id = session.get("user_id")
    result = chats_collection.delete_one({"chat_id": chat_id, "user_id": user_id})
    if result.deleted_count > 0:
        return jsonify({"message": "Chat eliminado"})
    return jsonify({"error": "No autorizado o no encontrado"}), 404

# NUEVO: Ruta para editar el título de un chat
@app.route("/api/chat/<chat_id>", methods=["PUT"])
def update_chat_title(chat_id):
    if chats_collection is None:
        return jsonify({"error": "No database"}), 500
    user_id = session.get("user_id")
    new_title = request.json.get("title")
    if not new_title:
        return jsonify({"error": "Título inválido"}), 400
    
    result = chats_collection.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"title": new_title}}
    )
    if result.modified_count > 0:
        return jsonify({"message": "Título actualizado"})
    return jsonify({"error": "No autorizado"}), 404

@app.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "Falta API Key."}), 500
    try:
        data = request.json
        user_message = data.get("message")
        chat_id = data.get("chatId")
        
        if not user_message:
            return jsonify({"error": "El mensaje no puede estar vacío"}), 400

        user_id = session.get("user_id")
        if not user_id:
            user_id = os.urandom(16).hex()
            session["user_id"] = user_id

        conversation_history = []
        is_new_chat = False
        
        if chat_id and chats_collection is not None:
            chat_data = chats_collection.find_one({"chat_id": chat_id})
            if chat_data:
                conversation_history = chat_data.get("history", [])
        else:
            chat_id = str(uuid.uuid4())
            is_new_chat = True

        max_history = 12
        recent_history = conversation_history[-max_history:] if len(conversation_history) > max_history else conversation_history
        context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_history])
        
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

        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt_with_history}]}]}
        
        response = requests.post(GEMINI_API_URL, headers=headers, params={"key": GEMINI_API_KEY}, json=payload)
        
        if response.status_code != 200:
            return jsonify({"error": "Error comunicando con el cerebro de IA."}), 502

        response_json = response.json()

        if "candidates" in response_json:
            bot_response = response_json["candidates"][0]["content"]["parts"][0]["text"]
            
            conversation_history.append({"role": "user", "content": user_message})
            conversation_history.append({"role": "bot", "content": bot_response})
            
            title = user_message[:30] + "..." if is_new_chat else "Sesión de Triaje"
            
            if chats_collection is not None:
                if not is_new_chat:
                    chat_data = chats_collection.find_one({"chat_id": chat_id})
                    if chat_data:
                        title = chat_data.get("title", title)
                        
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
        else:
            return jsonify({"error": "La IA devolvió una respuesta vacía."}), 500
            
    except Exception as e:
        print(f"Error CRÍTICO en /api/chat: {traceback.format_exc()}")
        return jsonify({"error": "Error interno del servidor Tamachi."}), 500

@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    return jsonify({"message": "Sesión reiniciada"})

if __name__ == "__main__":
    app.run(debug=True)