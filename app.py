from flask import Flask, render_template, request, jsonify, session
import requests
import os
import re
from dotenv import load_dotenv
# from pymongo import MongoClient

load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "chatbot_history"

if not GEMINI_API_KEY:
    print("Error: La variable de entorno GEMINI_API_KEY no está definida.")

if MONGO_URI:
    try:
        # client = MongoClient(MONGO_URI)
        # db = client[DB_NAME]
        # conversations = db.conversations
        print("Conexión a MongoDB Atlas establecida.")
    except Exception as e:
        print(f"Error al conectar a MongoDB: {e}")
        print("La funcionalidad de memoria persistente estará desactivada.")
        MONGO_URI = None
else:
    print("Advertencia: La variable de entorno MONGO_URI no está definida. La memoria persistente estará desactivada.")

def get_conversation_history(user_id, use_persistence=True):
    if use_persistence and MONGO_URI:
        # conversation_data = conversations.find_one({"user_id": user_id})
        # if conversation_data and "history" in conversation_data:
        #     return conversation_data["history"]
        # else:
        #     return []
        return []
    else:
        return session.get("conversation_history", [])

def store_conversation_history(user_id, history, use_persistence=True):
    if use_persistence and MONGO_URI:
        # conversations.update_one(
        #     {"user_id": user_id},
        #     {"$set": {"history": history}},
        #     upsert=True
        # )
        pass
    else:
        session["conversation_history"] = history
        session.modified = True

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
        store_conversation_history(user_id, conversation_history, use_persistence)

        return jsonify({"response": bot_response})

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    user_id = session.get("user_id")
    if user_id:
        store_conversation_history(user_id, [], True)
    session.pop("conversation_history", None)
    return jsonify({"status": "Chat reiniciado"})

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
        user_id = os.urandom(16).hex()
        session["user_id"] = user_id

    conversation_history = get_conversation_history(user_id, use_persistence)

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
        store_conversation_history(user_id, conversation_history, use_persistence)

        return jsonify({"response": bot_response})

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
