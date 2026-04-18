import os
import subprocess
from flask import Flask, render_template, request, send_file, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/convert", methods=["POST"])
def convert():
    if "video" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["video"]

    if file.filename == "":
        return jsonify({"error": "Arquivo inválido"}), 400

    input_path = os.path.join(UPLOAD_DIR, file.filename)
    output_name = os.path.splitext(file.filename)[0] + ".mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    file.save(input_path)

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", input_path,
                "-movflags", "faststart",
                "-pix_fmt", "yuv420p",
                output_path
            ],
            check=True
        )
    except subprocess.CalledProcessError:
        return jsonify({"error": "Erro ao converter o vídeo"}), 500

    return send_file(output_path, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
