import os
from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB upload

# =========================
# ROTAS DE INTERFACE
# =========================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    return render_template("login.html")


# =========================
# ROTAS DE API (EXEMPLO)
# =========================

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# =========================
# EXEMPLO DE UPLOAD / PROCESSAMENTO
# (ajuste conforme seu fluxo real)
# =========================

@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "Arquivo inválido"}), 400

    uploads_dir = os.path.join(BASE_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    file_path = os.path.join(uploads_dir, file.filename)
    file.save(file_path)

    # Aqui entraria FFmpeg / Whisper / Groq etc
    # Por enquanto, apenas devolve o arquivo salvo

    return jsonify({
        "message": "Arquivo recebido com sucesso",
        "filename": file.filename
    })


# =========================
# START
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
