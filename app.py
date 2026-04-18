from flask import Flask, send_from_directory, Response
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static")

# Página principal
@app.route("/")
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

# Manifest PWA (ESSENCIAL para PWABuilder)
@app.route("/static/manifest.json")
def manifest():
    return send_from_directory(
        os.path.join(BASE_DIR, "static"),
        "manifest.json",
        mimetype="application/manifest+json"
    )

# Service Worker
@app.route("/static/sw.js")
def service_worker():
    response = send_from_directory(
        os.path.join(BASE_DIR, "static"),
        "sw.js"
    )
    response.headers["Content-Type"] = "application/javascript"
    return response

# Health check (Render)
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
