from flask import Flask, render_template, send_from_directory
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")

# ROTA PRINCIPAL
@app.route("/")
def index():
    return render_template("index.html")

# MANIFEST PWA
@app.route("/static/manifest.json")
def manifest():
    return send_from_directory(
        os.path.join(BASE_DIR, "static"),
        "manifest.json",
        mimetype="application/manifest+json"
    )

# SERVICE WORKER
@app.route("/static/sw.js")
def service_worker():
    return send_from_directory(
        os.path.join(BASE_DIR, "static"),
        "sw.js",
        mimetype="application/javascript"
    )

# HEALTH CHECK
@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
