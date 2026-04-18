from flask import (
    Flask, request, send_file, after_this_request,
    session, redirect, url_for, jsonify
)
import subprocess, os, tempfile, traceback, sqlite3
import secrets, uuid, multiprocessing, json, textwrap
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
FONT_PATH    = os.path.join(BASE_DIR, "fonts", "Anton-Regular.ttf")

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
            user = conn.execute(
                "SELECT * FROM usuarios WHERE email = ?", (email,)
            ).fetchone()
        if user and check_password_hash(user["senha"], senha):
            token = str(uuid.uuid4())
            session.permanent = True
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
#  PÁGINAS
# ─────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

# ─────────────────────────────────────────────
#  PWA
# ─────────────────────────────────────────────
@app.route("/manifest.json")
def manifest():
    return send_file(os.path.join(BASE_DIR, "manifest.json"), mimetype="application/manifest+json")

@app.route("/service-worker.js")
def service_worker():
    resp = send_file(os.path.join(BASE_DIR, "service-worker.js"), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_file(os.path.join(BASE_DIR, "static", filename))

# ─────────────────────────────────────────────
#  TRANSCRIÇÃO — GROQ COM TIMESTAMPS
# ─────────────────────────────────────────────
def _groq_transcrever(audio_bytes, filename):
    """Retorna (texto, segmentos) com timestamps do Groq."""
    resp = http_requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename, audio_bytes, "audio/mpeg")},
        data={
            "model":           "whisper-large-v3-turbo",
            "language":        "pt",
            "response_format": "verbose_json",
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"Groq {resp.status_code}: {resp.text}")

    data = resp.json()
    texto = data.get("text", "").strip()
    segs  = [
        {
            "start": float(s.get("start", 0)),
            "end":   float(s.get("end",   0)),
            "text":  s.get("text", "").strip()
        }
        for s in data.get("segments", [])
    ]
    return texto, segs

@app.route("/transcrever", methods=["POST"])
@login_required
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        texto, segs = _groq_transcrever(aud.read(), aud.filename or "audio.mp3")
        return jsonify({"texto": texto, "segmentos": segs})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ─────────────────────────────────────────────
#  GERADOR ASS — estilo TikTok/CupCult
# ─────────────────────────────────────────────
def _ts_ass(s: float) -> str:
    """Segundos → H:MM:SS.cc (formato ASS)"""
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = int(s % 60)
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"

def gerar_ass(segmentos: list, w: int, h: int) -> str:
    font_name = "Anton" if os.path.exists(FONT_PATH) else "Impact"
    font_size = int(w * 0.068)
    margin_v  = int(h * 0.07)

    # Cores ASS: &HAABBGGRR
    c_white  = "&H00FFFFFF"
    c_black  = "&H00000000"
    c_box    = "&H99000000"   # caixa preta 60% opaca
    c_shadow = "&HAA000000"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tron,{font_name},{font_size},{c_white},{c_white},{c_black},{c_box},-1,0,0,0,100,100,0,0,3,5,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for seg in segmentos:
        txt = seg["text"].strip()
        if not txt:
            continue
        # Quebra em até 2 linhas se muito longo
        if len(txt) > 40:
            txt = textwrap.fill(txt, width=40, max_lines=2, placeholder="...").replace("\n", "\\N")
        # Remove chaves que quebram o ASS
        txt = txt.replace("{", "").replace("}", "")
        lines.append(
            f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},"
            f"Tron,,0,0,0,,{txt}"
        )

    return header + "\n".join(lines)

# ─────────────────────────────────────────────
#  FALLBACK — legenda estática (texto manual)
# ─────────────────────────────────────────────
def _esc(txt: str) -> str:
    return (txt
        .replace("\\", "\\\\").replace("'", "\\'")
        .replace(":",  "\\:" ).replace("[", "\\[")
        .replace("]",  "\\]" ).replace(",", "\\,")
    )

def build_vf_estatico(w: str, h: str, legenda: str) -> str:
    scale = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    if not legenda.strip():
        return scale
    txt  = _esc(legenda.strip())
    font = f"fontfile={FONT_PATH}" if os.path.exists(FONT_PATH) else "font=Impact"
    fs   = int(int(w) * 0.072)
    mb   = int(int(h) * 0.06)
    dt   = (
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
    img_file    = request.files.get("imagem")
    aud_file    = request.files.get("audio")
    resolucao   = request.form.get("resolucao", "1080x1080")
    legenda_txt = request.form.get("legenda", "").strip()
    modo_leg    = request.form.get("modo_legenda", "nenhuma")
    segs_json   = request.form.get("segmentos", "")

    # modo_legenda:
    #   "auto"     → legenda sincronizada (ASS com timestamps do Groq)
    #   "estatica" → texto manual fixo
    #   "nenhuma"  → sem legenda

    if not img_file or not aud_file:
        return "Imagem e áudio são obrigatórios.", 400

    w_str, h_str = RESOLUTIONS.get(resolucao, ("1080", "1080"))
    w, h = int(w_str), int(h_str)

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

            # ── Monta filtro de vídeo ──────────────────
            scale_vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            )
            vf       = scale_vf
            ass_path = None

            if modo_leg == "auto" and segs_json:
                try:
                    segs = json.loads(segs_json)
                    ass_content = gerar_ass(segs, w, h)
                    ass_path = os.path.join(tmp, "legenda.ass")
                    with open(ass_path, "w", encoding="utf-8") as f:
                        f.write(ass_content)
                    # No Linux o caminho não precisa de escape de ":"
                    vf = f"{scale_vf},ass={ass_path}"
                except Exception:
                    ass_path = None  # fallback

            if ass_path is None and modo_leg == "estatica" and legenda_txt:
                vf = build_vf_estatico(w_str, h_str, legenda_txt)

            cmd = [
                "ffmpeg", "-y",
                "-framerate", "1",
                "-loop", "1", "-i", img_path,
                "-i", aud_path,
                "-vf", vf,
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

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500
