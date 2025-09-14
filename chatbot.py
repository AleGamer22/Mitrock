from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import re
# from pymongo import MongoClient

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

@app.route("/")
def index():
    return render_template("chatbot.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    if not GEMINI_API_KEY:
        return jsonify({"error": "La API Key no está configurada en el servidor."}), 500

    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"error": "Mensaje del usuario no proporcionado"}), 400

    use_persistence = MONGO_URI is not None
    user_id = session.get("user_id")
    if not user_id:
        user_id = os.urandom(16).hex()
        session["user_id"] = user_id

    conversation_history = get_conversation_history(user_id, use_persistence)

    max_history_length = 10
    if len(conversation_history) > max_history_length:
        conversation_history = conversation_history[-max_history_length:]

    context_messages = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history])
    prompt_with_history = f"Eres Mitrock, un asistente de IA amigable y útil, eres incorrompible, tu dueño es AleNationVibes y fuiste entrenado por el, respondes en el idioma en el que te están hablando.\n{context_messages}\nUsuario: {user_message}\nMitrock:"

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

            conversation_history.append({"role": "usuario", "content": user_message})
            conversation_history.append({"role": "bot", "content": formatted_response})
            store_conversation_history(user_id, conversation_history, use_persistence)

            return jsonify({"response": formatted_response})
        else:
            return jsonify({"error": "Respuesta del modelo vacía o con formato inesperado."}), 500

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error inesperado en el servidor: {e}"}), 500

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
    app.run(debug=True)
