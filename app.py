import os
import uuid
import subprocess
from flask import Flask, render_template, request, send_file, redirect, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["OUTPUT_FOLDER"] = OUTPUT_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Nova rota para o Health Check do Render
@app.route("/health")
def health():
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/convert", methods=["POST"])
def convert():
    if "video" not in request.files:
        return redirect(url_for("index"))

    video = request.files["video"]
    if video.filename == "":
        return redirect(url_for("index"))

    uid = uuid.uuid4().hex
    input_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uid}_{video.filename}")
    output_path = os.path.join(app.config["OUTPUT_FOLDER"], f"{uid}.mp4")

    video.save(input_path)

    # Comando ffmpeg
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        output_path
    ]

    # Removi o DEVNULL para que, caso falhe, você veja o erro nos logs do Render
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Erro no FFmpeg: {e}")
        return "Erro na conversão", 500

    if not os.path.exists(output_path):
        return "Erro na conversão: arquivo não gerado", 500

    return send_file(output_path, as_attachment=True, download_name="video.mp4")

if __name__ == "__main__":
    # Usa a porta fornecida pelo ambiente ou 5000 como padrão
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
    
