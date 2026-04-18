from flask import (
    Flask, request, send_file, after_this_request,
    session, redirect, url_for, jsonify
)
import subprocess, os, tempfile, traceback, sqlite3
import secrets, uuid, multiprocessing
import requests as http_requests
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CPU_CORES    = str(multiprocessing.cpu_count())
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "fonts", "Anton-Regular.ttf")

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
#  AUTH ROUTES
# ─────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
        if user and check_password_hash(user["senha"], senha):
            token = str(uuid.uuid4())
            session["user"]  = email
            session["token"] = token
            with get_db() as conn:
                conn.execute(
                    "UPDATE usuarios SET sessao_atual = ? WHERE email = ?",
                    (token, email)
                )
            return redirect(url_for("index"))
        return "E-mail ou senha incorretos.", 401
    return open(os.path.join(BASE_DIR, "login.html"), encoding="utf-8").read()

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/criar/<senha_mestra>/<email_cliente>")
def criar_acesso(senha_mestra, email_cliente):
    if senha_mestra != os.environ.get("ADMIN_KEY", "jon369"):
        return "Acesso negado.", 403
    senha_plana = secrets.token_hex(4)
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO usuarios (email, senha, ativo) VALUES (?, ?, 1)",
            (email_cliente.lower(), generate_password_hash(senha_plana))
        )
    return f"""
    <h2 style='font-family:monospace'>✅ Acesso criado</h2>
    <p><b>Email:</b> {email_cliente}</p>
    <p><b>Senha:</b> {senha_plana}</p>
    <p>Envie ao cliente via WhatsApp.</p>
    """

# ─────────────────────────────────────────────
#  INDEX
# ─────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

# ─────────────────────────────────────────────
#  TRANSCRIÇÃO — GROQ WHISPER
# ─────────────────────────────────────────────
@app.route("/transcrever", methods=["POST"])
@login_required
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada no servidor."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        resp = http_requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (aud.filename or "audio.mp3", aud.read(), "audio/mpeg")},
            data={"model": "whisper-large-v3-turbo", "language": "pt", "response_format": "text"},
            timeout=120
        )
        if resp.status_code != 200:
            return jsonify({"erro": f"Groq: {resp.text}"}), 500
        return jsonify({"texto": resp.text.strip()})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ─────────────────────────────────────────────
#  HELPERS FFmpeg
# ─────────────────────────────────────────────
def _esc(txt: str) -> str:
    return (txt
        .replace("\\", "\\\\").replace("'", "\\'")
        .replace(":",  "\\:" ).replace("[", "\\[")
        .replace("]",  "\\]" ).replace(",", "\\,")
    )

def build_vf(w: str, h: str, legenda: str) -> str:
    scale = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    if not legenda or not legenda.strip():
        return scale

    txt  = _esc(legenda.strip())
    font = f"fontfile={FONT_PATH}" if os.path.exists(FONT_PATH) else "font=Impact"
    fs   = int(int(w) * 0.072)
    mb   = int(int(h) * 0.06)

    dt = (
        f"drawtext={font}:text='{txt}':"
        f"fontcolor=white:fontsize={fs}:"
        f"bordercolor=black:borderw=5:"
        f"shadowcolor=black@0.65:shadowx=2:shadowy=3:"
        f"box=1:boxcolor=black@0.38:boxborderw=14:"
        f"x=(w-text_w)/2:y=h-text_h-{mb}"
    )
    return f"{scale},{dt}"

# ─────────────────────────────────────────────
#  CONVERSOR
# ─────────────────────────────────────────────
@app.route("/converter", methods=["POST"])
@login_required
def converter():
    img_file  = request.files.get("imagem")
    aud_file  = request.files.get("audio")
    resolucao = request.form.get("resolucao", "1080x1080")
    legenda   = request.form.get("legenda", "").strip()

    if not img_file or not aud_file:
        return "Imagem e áudio são obrigatórios.", 400

    w, h = RESOLUTIONS.get(resolucao, ("1080", "1080"))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_ext  = os.path.splitext(img_file.filename)[1] or ".jpg"
            aud_ext  = os.path.splitext(aud_file.filename)[1] or ".mp3"
            img_path = os.path.join(tmp, "img" + img_ext)
            aud_path = os.path.join(tmp, "aud" + aud_ext)
            img_file.save(img_path)
            aud_file.save(aud_path)

            fd, out_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            cmd = [
                "ffmpeg", "-y",
                "-framerate", "1",
                "-loop", "1", "-i", img_path,
                "-i", aud_path,
                "-vf", build_vf(w, h, legenda),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-crf", "40",
                "-pix_fmt", "yuv420p",
                "-threads", CPU_CORES,
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-shortest",
                "-movflags", "+faststart",
                "-bufsize", "8M",
                out_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)

        if result.returncode != 0:
            return f"Erro FFmpeg:\n{result.stderr[-1500:]}", 500

        @after_this_request
        def _cleanup(response):
            try: os.unlink(out_path)
            except Exception: pass
            return response

        return send_file(out_path, mimetype="video/mp4",
                         as_attachment=True, download_name="tron_clipe.mp4")

    except subprocess.TimeoutExpired:
        return "Tempo limite excedido (20 min).", 504
    except Exception:
        return f"Erro interno:\n{traceback.format_exc()}", 500

# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ─────────────────────────────────────────────
#  HEALTH CHECK — Render usa isso para saber se o app subiu
# ─────────────────────────────────────────────
@app.route("/healthz")
def healthz():
    return "OK", 200

# ─────────────────────────────────────────────
#  ERRO GLOBAL — mostra traceback real no browser para facilitar debug
# ─────────────────────────────────────────────
@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500
