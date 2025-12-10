from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import re
from pymongo import MongoClient
from uuid import uuid4
from datetime import datetime

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "chatbot_history"

if not GEMINI_API_KEY:
    print("Error: La variable de entorno GEMINI_API_KEY no está definida.")

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client[DB_NAME]
        conversations = db.conversations
        print("Conexión a MongoDB Atlas establecida.")
    except Exception as e:
        print(f"Error al conectar a MongoDB: {e}")
        print("La funcionalidad de memoria persistente estará desactivada.")
        MONGO_URI = None
else:
    print("Advertencia: La variable de entorno MONGO_URI no está definida. La memoria persistente estará desactivada.")

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

def make_chat_doc(user_id, title="Nuevo chat", model="gemini"):
    return {
        "chat_id": uuid4().hex,
        "user_id": user_id,
        "title": title,
        "model": model,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "messages": []
    }

def _get_user_id():
    uid = session.get("user_id")
    if not uid:
        uid = os.urandom(16).hex()
        session["user_id"] = uid
    return uid

def _find_chat(user_id, chat_id):
    if MONGO_URI:
        return conversations.find_one({"chat_id": chat_id, "user_id": user_id})
    else:
        for c in session.get("chats", []):
            if c["chat_id"] == chat_id:
                return c
    return None

def _save_chat_doc(chat):
    # upsert in mongo or update session
    if MONGO_URI:
        conversations.update_one({"chat_id": chat["chat_id"], "user_id": chat["user_id"]}, {"$set": chat}, upsert=True)
    else:
        chats = session.get("chats", [])
        for i, c in enumerate(chats):
            if c["chat_id"] == chat["chat_id"]:
                chats[i] = chat
                session["chats"] = chats
                return
        chats.append(chat)
        session["chats"] = chats

# Simple adapter: call Gemini or OpenAI depending on model string
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def call_model(model_name, prompt_text):
    # model_name: "gemini" or "openai" (or "gpt" etc)
    if model_name and model_name.lower().startswith("openai"):
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY no configurada")
        # placeholder: use OpenAI Chat Completions (user should install openai lib)
        # Aquí dejamos un stub simple para evitar dependencia en el parche
        # Reemplaza por llamada real a openai.ChatCompletion.create(...) en producción
        return f"(simulated OpenAI response para prompt length {len(prompt_text)})"
    else:
        # Gemini (usa GEMINI_API_KEY)
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY no configurada")
        headers = {"Content-Type": "application/json"}
        params = {"key": GEMINI_API_KEY}
        data = {"contents":[{"parts":[{"text": prompt_text}]}]}
        resp = requests.post(GEMINI_API_URL, headers=headers, params=params, json=data, timeout=30)
        resp.raise_for_status()
        j = resp.json()
        if "candidates" in j and j["candidates"]:
            return j["candidates"][0]["content"]["parts"][0]["text"]
        raise RuntimeError("Respuesta inesperada del modelo")

# Chats list / create
@app.route("/api/chats", methods=["GET", "POST"])
def chats_list_create():
    user_id = _get_user_id()
    if request.method == "GET":
        if MONGO_URI:
            rows = list(conversations.find({"user_id": user_id}, {"messages": 0}))
            for r in rows: r["_id"] = str(r["_id"])
            return jsonify(rows)
        else:
            return jsonify(session.get("chats", []))
    # POST -> create
    body = request.get_json() or {}
    title = body.get("title", "Nuevo chat")
    model = body.get("model", "gemini")
    chat = make_chat_doc(user_id, title=title, model=model)
    _save_chat_doc(chat)
    return jsonify({"chat_id": chat["chat_id"], "title": chat["title"], "model": chat["model"]})

# Get / delete / patch a chat
@app.route("/api/chats/<chat_id>", methods=["GET","DELETE","PATCH"])
def chat_get_delete_patch(chat_id):
    user_id = _get_user_id()
    chat = _find_chat(user_id, chat_id)
    if request.method == "GET":
        if not chat: return jsonify({"error":"not_found"}), 404
        # hide internal fields as needed
        return jsonify(chat)
    if request.method == "DELETE":
        if MONGO_URI:
            conversations.delete_one({"chat_id": chat_id, "user_id": user_id})
        else:
            session["chats"] = [c for c in session.get("chats", []) if c["chat_id"] != chat_id]
        return jsonify({"ok": True})
    if request.method == "PATCH":
        body = request.get_json() or {}
        if not chat: return jsonify({"error":"not_found"}), 404
        if "title" in body: chat["title"] = body["title"]
        if "model" in body: chat["model"] = body["model"]
        chat["updated_at"] = datetime.utcnow()
        _save_chat_doc(chat)
        return jsonify({"ok": True})

# Send message to a chat (core)
@app.route("/api/chats/<chat_id>/msg", methods=["POST"])
def chat_send_message(chat_id):
    user_id = _get_user_id()
    data = request.get_json() or {}
    message = data.get("message", "")
    if not message:
        return jsonify({"error":"no_message"}), 400
    chat = _find_chat(user_id, chat_id)
    if not chat:
        return jsonify({"error":"chat_not_found"}), 404
    # append user message
    msg_obj = {"role":"user","content": message, "ts": datetime.utcnow().isoformat()}
    chat.setdefault("messages", []).append(msg_obj)
    chat["updated_at"] = datetime.utcnow()
    _save_chat_doc(chat)
    # build prompt (you can change format)
    context = "\n".join([f"{m['role']}: {m['content']}" for m in chat.get("messages", [])][-20:])
    prompt = f"Eres Mitrock, asistente. {context}\nuser: {message}\nMitrock:"
    try:
        raw = call_model(chat.get("model","gemini"), prompt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    # basic markdown conversion (server-side) — keep as HTML for allowed tags
    formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", raw)
    bot_obj = {"role":"bot","content": formatted, "ts": datetime.utcnow().isoformat()}
    chat["messages"].append(bot_obj)
    _save_chat_doc(chat)
    return jsonify({"response": formatted})

# Reset chat messages
@app.route("/api/chats/<chat_id>/reset", methods=["POST"])
def chat_reset(chat_id):
    user_id = _get_user_id()
    chat = _find_chat(user_id, chat_id)
    if not chat: return jsonify({"error":"chat_not_found"}), 404
    chat["messages"] = []
    chat["updated_at"] = datetime.utcnow()
    _save_chat_doc(chat)
    return jsonify({"ok": True})

# Regenerate last bot response for a chat
@app.route("/api/chats/<chat_id>/regenerate", methods=["POST"])
def chat_regenerate(chat_id):
    user_id = _get_user_id()
    body = request.get_json() or {}
    # If message supplied use it, else infer previous user message
    override_message = body.get("message")
    chat = _find_chat(user_id, chat_id)
    if not chat: return jsonify({"error":"chat_not_found"}), 404
    history = chat.get("messages", [])
    # find last user message to regenerate for
    last_user = override_message
    if not last_user:
        for i in range(len(history)-1, -1, -1):
            if history[i]["role"] == "user":
                last_user = history[i]["content"]
                break
    if not last_user: return jsonify({"error":"no_user_message_found"}), 400
    # remove last bot if present
    if history and history[-1]["role"] == "bot":
        history.pop()
    prompt = "\n".join([f"{m['role']}: {m['content']}" for m in history]) + f"\nuser: {last_user}\nMitrock:"
    try:
        raw = call_model(chat.get("model","gemini"), prompt)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", raw)
    history.append({"role":"bot","content":formatted, "ts": datetime.utcnow().isoformat()})
    chat["messages"] = history
    _save_chat_doc(chat)
    return jsonify({"response": formatted})

# Optional: keep existing /api/chat for quick compatibility by routing to active chat
@app.route("/api/chat", methods=["POST"])
def legacy_chat():
    active = session.get("active_chat")
    if not active:
        # create a default chat
        user_id = _get_user_id()
        chat = make_chat_doc(user_id)
        _save_chat_doc(chat)
        session["active_chat"] = chat["chat_id"]
        active = chat["chat_id"]
    return chat_send_message(active)

if __name__ == "__main__":
    app.run(debug=True)
