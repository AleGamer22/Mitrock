from flask import Flask, render_template, request, jsonify

app = Flask(__name__, static_folder="static", template_folder="templates")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/ia")
def ia():
    return render_template("chatbot.html")

@app.route("/chat", methods=["POST"])
def chat():
    payload = request.get_json(silent=True) or {}
    user_message = payload.get("message", "")

    # Aquí puedes integrar tu lógica de IA real.
    return jsonify({
        "status": "ok",
        "message": "Petición recibida",
        "received": user_message,
    })

if __name__ == "__main__":
    app.run(debug=True)
