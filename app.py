from flask import (
    Flask, request, send_file, after_this_request,
    session, redirect, url_for, jsonify
)
import subprocess, os, tempfile, traceback, sqlite3
import secrets, uuid, multiprocessing, json, textwrap, shutil
import requests as http_requests
from datetime import timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "tron-emc3-chave-fixa-2025")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CPU_CORES    = str(multiprocessing.cpu_count())
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR    = os.path.join(BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

# Procura a fonte em várias localizações; copia para FONTS_DIR se necessário
_FONT_CANDIDATES = [
    (os.path.join(FONTS_DIR, "Autumn_Regular.ttf"), "Autumn"),
    (os.path.join(BASE_DIR,  "Autumn_Regular.ttf"),  "Autumn"),
    (os.path.join(FONTS_DIR, "Anton-Regular.ttf"),   "Anton"),
    (os.path.join(BASE_DIR,  "Anton-Regular.ttf"),   "Anton"),
]
FONT_PATH, FONT_NAME = next(
    ((p, n) for p, n in _FONT_CANDIDATES if os.path.exists(p)),
    ("", "Impact")
)

# Garante que a fonte esteja dentro de FONTS_DIR (para o fontsdir do FFmpeg)
if FONT_PATH and not FONT_PATH.startswith(FONTS_DIR):
    _dest = os.path.join(FONTS_DIR, os.path.basename(FONT_PATH))
    if not os.path.exists(_dest):
        shutil.copy2(FONT_PATH, _dest)
    FONT_PATH = _dest

RESOLUTIONS = {
    "720x1280":  ("720",  "1280"),
    "1080x1080": ("1080", "1080"),
    "1280x720":  ("1280", "720"),
}

# ─────────────────────────────────────────────
#  BANCO DE DADOS
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "usuarios.db"))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                email        TEXT PRIMARY KEY,
                senha        TEXT NOT NULL,
                sessao_atual TEXT,
                ativo        INTEGER DEFAULT 1
            )
        """)
        try:
            conn.execute("ALTER TABLE usuarios ADD COLUMN sessao_atual TEXT")
        except Exception:
            pass

init_db()

# ─────────────────────────────────────────────
#  SEGURANÇA
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session or "token" not in session:
            return redirect(url_for("login"))
        with get_db() as conn:
            user = conn.execute(
                "SELECT sessao_atual FROM usuarios WHERE email = ?",
                (session["user"],)
            ).fetchone()
        if not user or user["sessao_atual"] != session["token"]:
            session.clear()
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ─────────────────────────────────────────────
#  AUTH
# ─────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        with get_db() as conn:
            
