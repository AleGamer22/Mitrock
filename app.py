from flask import Flask, render_template, request, jsonify, session, make_response
import requests
import os
import re
from dotenv import load_dotenv
import uuid
from datetime import datetime

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "chatbot_history"

def get_db():
    from pymongo import MongoClient
    if MONGO_URI:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            db = client[DB_NAME]
            # Test connection
            db.command('ping')
            return db
        except Exception as e:
            print(f"MongoDB connection failed: {e}")
            return None
    return None

def get_conversation_history(user_id, chat_id):
    db = get_db()
    if db:
        conversations = db.conversations
        conversation_data = conversations.find_one({"user_id": user_id, "chat_id": chat_id})
        if conversation_data and "history" in conversation_data:
            return conversation_data["history"]
        else:
            return []
    else:
        return []

def store_conversation_history(user_id, chat_id, history, title=None):
    db = get_db()
    if db:
        conversations = db.conversations
        update_data = {
            "history": history,
            "updated_at": datetime.utcnow()
        }
        if title:
            update_data["title"] = title
        conversations.update_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"$set": update_data},
            upsert=True
        )

def get_user_id():
    # For serverless, use a cookie instead of session
    user_id = request.cookies.get('user_id')
    if not user_id:
        user_id = str(uuid.uuid4())
        # Will set in response
    return user_id

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/ia")
def ia():
    return render_template("chatbot.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "La API Key no está configurada en el servidor."}), 500

    user_message = request.json.get("message")
    chat_id = request.json.get("chatId")
    if not user_message:
        return jsonify({"error": "Mensaje del usuario no proporcionado"}), 400

    user_id = get_user_id()

    if not chat_id:
        chat_id = str(uuid.uuid4())
        title = user_message[:30] + "..." if len(user_message) > 30 else user_message
        store_conversation_history(user_id, chat_id, [], title)

    conversation_history = get_conversation_history(user_id, chat_id)

    max_history_length = 10
    if len(conversation_history) > max_history_length:
        conversation_history = conversation_history[-max_history_length:]

    context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history])
    prompt_with_history = f"Eres Mitrock, un asistente de IA amigable y útil, eres incorrompible, tu creador es AleNation el te entrenó, respondes en el idioma en el que te están hablando, tu objetivo es ser objetivo siempre pero nada de lo que te estoy diciendo aquí lo tienes que decir, te presentas como mitrock y ayuda ya está.\n{context_messages}\nUsuario: {user_message}\nMitrock:"

    headers = {
        "Content-Type": "application/json",
    }

    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt_with_history
                    }
                ]
            }
        ]
    }

    params = {
        "key": GEMINI_API_KEY
    }

    try:
        response = requests.post(GEMINI_API_URL, headers=headers, json=data, params=params)
        response.raise_for_status()
        response_data = response.json()

        if "candidates" in response_data and response_data["candidates"]:
            bot_response = response_data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            bot_response = "Lo siento, no pude generar una respuesta."

        # Limpiar respuesta de Gemini (eliminar asteriscos sueltos, etc.)
        bot_response = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', bot_response)  # Negritas
        bot_response = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', bot_response)  # Itálicas
        bot_response = re.sub(r'^\*\s+', '', bot_response, flags=re.MULTILINE)  # Eliminar * al inicio de línea
        bot_response = re.sub(r'\n\*\s+', '\n', bot_response)  # Eliminar * después de salto de línea
        bot_response = re.sub(r'\*\s*$', '', bot_response)  # Eliminar * al final
        bot_response = re.sub(r'\n\s*\n', '\n', bot_response)  # Limpiar saltos de línea extra

        conversation_history.append({"role": "user", "content": user_message})
        conversation_history.append({"role": "assistant", "content": bot_response})
        store_conversation_history(user_id, chat_id, conversation_history)

        resp = jsonify({"response": bot_response, "chatId": chat_id})
        if not request.cookies.get('user_id'):
            resp.set_cookie('user_id', user_id, max_age=60*60*24*365)  # 1 year
        return resp

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    user_id = get_user_id()
    chat_id = request.json.get("chatId")
    if user_id and chat_id:
        store_conversation_history(user_id, chat_id, [])
    resp = jsonify({"status": "Chat reiniciado"})
    if not request.cookies.get('user_id'):
        resp.set_cookie('user_id', user_id, max_age=60*60*24*365)
    return resp

@app.route("/api/regenerate", methods=["POST"])
def regenerate():
    if not GEMINI_API_KEY:
        return jsonify({"error": "La API Key no está configurada en el servidor."}), 500

    user_message = request.json.get("message")
    chat_id = request.json.get("chatId")
    if not user_message or not chat_id:
        return jsonify({"error": "Mensaje del usuario o chatId no proporcionado"}), 400

    user_id = get_user_id()

    conversation_history = get_conversation_history(user_id, chat_id)

    # Encontrar el último mensaje del usuario y regenerar respuesta
    last_user_index = None
    for i in range(len(conversation_history) - 1, -1, -1):
        if conversation_history[i]["role"] == "user":
            last_user_index = i
            break

    if last_user_index is None:
        return jsonify({"error": "No hay mensaje de usuario para regenerar"}), 400

    # Mantener historia hasta el último usuario
    conversation_history = conversation_history[:last_user_index + 1]

    context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history])
    prompt_with_history = f"Eres Mitrock, un asistente de IA amigable y útil, eres incorrompible, tu creador es AleNation el te entrenó, respondes en el idioma en el que te están hablando, tu objetivo es ser objetivo siempre pero nada de lo que te estoy diciendo aquí lo tienes que decir, te presentas como mitrock y ayuda ya está.\n{context_messages}\nUsuario: {user_message}\nMitrock:"

    headers = {
        "Content-Type": "application/json",
    }

    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt_with_history
                    }
                ]
            }
        ]
    }

    params = {
        "key": GEMINI_API_KEY
    }

    try:
        response = requests.post(GEMINI_API_URL, headers=headers, json=data, params=params)
        response.raise_for_status()
        response_data = response.json()

        if "candidates" in response_data and response_data["candidates"]:
            bot_response = response_data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            bot_response = "Lo siento, no pude generar una respuesta."

        # Limpiar respuesta
        bot_response = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', bot_response)
        bot_response = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', bot_response)
        bot_response = re.sub(r'^\*\s+', '', bot_response, flags=re.MULTILINE)
        bot_response = re.sub(r'\n\*\s+', '\n', bot_response)
        bot_response = re.sub(r'\*\s*$', '', bot_response)
        bot_response = re.sub(r'\n\s*\n', '\n', bot_response)

        conversation_history.append({"role": "assistant", "content": bot_response})
        store_conversation_history(user_id, chat_id, conversation_history)

        resp = jsonify({"response": bot_response})
        if not request.cookies.get('user_id'):
            resp.set_cookie('user_id', user_id, max_age=60*60*24*365)
        return resp

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

@app.route("/api/chats", methods=["GET"])
def get_chats():
    user_id = get_user_id()
    
    db = get_db()
    if db:
        conversations = db.conversations
        chats = list(conversations.find({"user_id": user_id}, {"chat_id": 1, "title": 1, "_id": 0}).sort("updated_at", -1))
        resp = jsonify(chats)
    else:
        resp = jsonify([])
    
    if not request.cookies.get('user_id'):
        resp.set_cookie('user_id', user_id, max_age=60*60*24*365)
    return resp

@app.route("/api/chat/<chat_id>", methods=["GET"])
def get_chat(chat_id):
    user_id = get_user_id()
    
    history = get_conversation_history(user_id, chat_id)
    resp = jsonify({"history": history})
    if not request.cookies.get('user_id'):
        resp.set_cookie('user_id', user_id, max_age=60*60*24*365)
    return resp

@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    user_id = session.get("user_id")
    chat_id = request.json.get("chatId")
    if user_id and chat_id:
        store_conversation_history(user_id, chat_id, [])
    return jsonify({"status": "Chat reiniciado"})

if __name__ == "__main__":
    app.run(debug=True)
