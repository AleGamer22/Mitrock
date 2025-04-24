from flask import Flask, request, jsonify, render_template, session
import requests
import os
from dotenv import load_dotenv
import re
import datetime
import locale

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your_secret_key")  # ¡Importante para usar session!

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

if not GEMINI_API_KEY:
    print("Error: La variable de entorno GEMINI_API_KEY no está definida.")


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

    if "conversation_history" not in session:
        session["conversation_history"] = []

    conversation_history = session["conversation_history"]

    max_history_length = 5
    if len(conversation_history) > max_history_length:
        conversation_history = conversation_history[-max_history_length:]
        session["conversation_history"] = conversation_history

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

            session["conversation_history"].append({"role": "usuario", "content": user_message})
            session["conversation_history"].append({"role": "bot", "content": formatted_response})
            session.modified = True

            return jsonify({"response": formatted_response})
        else:
            return jsonify({"error": "Respuesta del modelo vacía o con formato inesperado."}), 500

    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Error al comunicarse con la API de Gemini: {e}"}), 500
    except Exception as e:
        return jsonify({"error": f"Error inesperado en el servidor: {e}"}), 500


@app.route("/api/reset_chat", methods=["POST"])
def reset_chat():
    session.pop("conversation_history", None)
    return jsonify({"message": "Chat history cleared"})


if __name__ == "__main__":
    app.run(debug=True)
