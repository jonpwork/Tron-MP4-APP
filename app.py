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
#  STATUS (checado pelo frontend ao carregar)
# ─────────────────────────────────────────────
@app.route("/status")
def status():
    return jsonify({
        "groq":      bool(GROQ_API_KEY),
        "font_name": FONT_NAME,
        "font_ok":   bool(FONT_PATH),
    })

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
#  TRANSCRIÇÃO — GROQ COM TIMESTAMPS POR PALAVRA
# ─────────────────────────────────────────────
def _groq_transcrever(audio_bytes, filename):
    """
    Retorna (texto, segmentos, palavras).
    Usa timestamp_granularities=word para obter timing preciso por palavra.
    """
    resp = http_requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename, audio_bytes, "audio/mpeg")},
        data={
            "model":                     "whisper-large-v3-turbo",
            "language":                  "pt",
            "response_format":           "verbose_json",
            "timestamp_granularities[]": "word",   # ← timestamps por palavra!
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"Groq {resp.status_code}: {resp.text}")

    data  = resp.json()
    texto = data.get("text", "").strip()

    segs = [
        {
            "start": float(s.get("start", 0)),
            "end":   float(s.get("end",   0)),
            "text":  s.get("text", "").strip()
        }
        for s in data.get("segments", [])
    ]

    # Palavras individuais com timestamps precisos
    palavras = [
        {
            "word":  w.get("word", "").strip(),
            "start": float(w.get("start", 0)),
            "end":   float(w.get("end",   0)),
        }
        for w in data.get("words", [])
        if w.get("word", "").strip()
    ]

    return texto, segs, palavras

@app.route("/transcrever", methods=["POST"])
@login_required
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada no servidor."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        texto, segs, palavras = _groq_transcrever(aud.read(), aud.filename or "audio.mp3")
        return jsonify({"texto": texto, "segmentos": segs, "palavras": palavras})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ─────────────────────────────────────────────
#  GERADOR ASS — estilo TikTok karaoke palavra por palavra
# ─────────────────────────────────────────────
def _ts_ass(s: float) -> str:
    """Segundos → H:MM:SS.cc (formato ASS)"""
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = int(s % 60)
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"

PALAVRAS_POR_GRUPO = 4   # palavras exibidas por "quadro" na tela

def gerar_ass(dados: list, w: int, h: int, modo_dados: str = "segmentos") -> str:
    """
    modo_dados = "palavras"  → lista de {word, start, end}  ← TikTok karaoke
    modo_dados = "segmentos" → lista de {text, start, end}  ← fallback simples
    """
    font_size = int(w * 0.074)
    margin_v  = int(h * 0.08)

    # Cores ASS &HAABBGGRR
    c_branco   = "&H00FFFFFF"   # palavra já dita → branco sólido
    c_apagado  = "&H88AAAAAA"   # palavra ainda não dita → cinza suave
    c_contorno = "&H00000000"   # contorno preto
    c_caixa    = "&HAA000000"   # caixa preta 67% opaca

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tron,{FONT_NAME},{font_size},{c_branco},{c_apagado},{c_contorno},{c_caixa},-1,0,0,0,100,100,0,0,3,6,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []

    if modo_dados == "palavras" and dados:
        # ── MODO TikTok KARAOKE — palavra por palavra ──────────────────────
        # Cada grupo de PALAVRAS_POR_GRUPO palavras vira uma linha na tela.
        # Dentro da linha, \k<cs> anima a palavra no momento certo.
        # SecondaryColour (cinza) = não falada ainda
        # PrimaryColour  (branco) = sendo / já falada
        grupos = [
            dados[i : i + PALAVRAS_POR_GRUPO]
            for i in range(0, len(dados), PALAVRAS_POR_GRUPO)
        ]

        for grupo in grupos:
            if not grupo:
                continue
            start = grupo[0]["start"]
            end   = grupo[-1]["end"]
            if end <= start:
                end = start + 0.5

            partes = []
            for palavra in grupo:
                dur_cs = max(1, int(round((palavra["end"] - palavra["start"]) * 100)))
                texto_w = (
                    palavra["word"]
                    .strip()
                    .replace("{", "").replace("}", "")
                    .replace("\\", "")
                )
                partes.append(f"{{\\k{dur_cs}}}{texto_w}")

            texto_linha = " ".join(partes)
            lines.append(
                f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},"
                f"Tron,,0,0,0,,{texto_linha}"
            )

    else:
        # ── MODO SEGMENTO — fallback se não há timestamps por palavra ──────
        for seg in dados:
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            if len(txt) > 35:
                txt = (
                    textwrap.fill(txt, width=35, max_lines=2, placeholder="...")
                    .replace("\n", "\\N")
                )
            txt = txt.replace("{", "").replace("}", "")
            lines.append(
                f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},"
                f"Tron,,0,0,0,,{txt}"
            )

    return header + "\n".join(lines)

# ─────────────────────────────────────────────
#  FALLBACK — legenda estática (texto manual drawtext)
# ─────────────────────────────────────────────
def _esc(txt: str) -> str:
    return (
        txt.replace("\\", "\\\\")
           .replace("'",  "\\'")
           .replace(":",  "\\:")
           .replace("[",  "\\[")
           .replace("]",  "\\]")
           .replace(",",  "\\,")
    )

def build_vf_estatico(w: str, h: str, legenda: str) -> str:
    scale = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    )
    if not legenda.strip():
        return scale

    txt  = _esc(legenda.strip())
    font = f"fontfile={FONT_PATH}" if FONT_PATH else f"font={FONT_NAME}"
    fs   = int(int(w) * 0.074)
    mb   = int(int(h) * 0.06)
    dt   = (
        f"drawtext={font}:text='{txt}':"
        f"fontcolor=white:fontsize={fs}:"
        f"bordercolor=black:borderw=6:"
        f"shadowcolor=black@0.65:shadowx=2:shadowy=3:"
        f"box=1:boxcolor=black@0.40:boxborderw=14:"
        f"x=(w-text_w)/2:y=h-text_h-{mb}"
    )
    return f"{scale},{dt}"

# ─────────────────────────────────────────────
#  CONVERSOR PRINCIPAL
# ─────────────────────────────────────────────
@app.route("/converter", methods=["POST"])
@login_required
def converter():
    img_file      = request.files.get("imagem")
    aud_file      = request.files.get("audio")
    resolucao     = request.form.get("resolucao",    "1080x1080")
    legenda_txt   = request.form.get("legenda",      "").strip()
    modo_leg      = request.form.get("modo_legenda", "nenhuma")
    palavras_json = request.form.get("palavras",     "")
    segs_json     = request.form.get("segmentos",    "")

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

            # ── Filtro de escala base ──────────────────────────────────────
            scale_vf = (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            )
            vf       = scale_vf
            ass_path = None

            # ── Legenda automática (TikTok karaoke) ───────────────────────
            if modo_leg == "auto":
                dados_ass  = None
                modo_dados = "segmentos"

                # Tenta palavras primeiro (efeito TikTok perfeito)
                if palavras_json:
                    try:
                        palavras = json.loads(palavras_json)
                        if palavras:
                            dados_ass  = palavras
                            modo_dados = "palavras"
                    except Exception:
                        pass

                # Fallback: segmentos
                if dados_ass is None and segs_json:
                    try:
                        dados_ass = json.loads(segs_json)
                    except Exception:
                        pass

                if dados_ass:
                    try:
                        ass_content = gerar_ass(dados_ass, w, h, modo_dados)
                        ass_path    = os.path.join(tmp, "legenda.ass")
                        with open(ass_path, "w", encoding="utf-8") as f:
                            f.write(ass_content)

                        # fontsdir garante que a fonte customizada seja encontrada
                        if os.path.isdir(FONTS_DIR):
                            vf = f"{scale_vf},ass={ass_path}:fontsdir={FONTS_DIR}"
                        else:
                            vf = f"{scale_vf},ass={ass_path}"

                    except Exception as err:
                        app.logger.error(f"Erro ao gerar ASS: {err}\n{traceback.format_exc()}")
                        ass_path = None  # rende sem legenda

            # ── Legenda estática (drawtext) ────────────────────────────────
            if ass_path is None and modo_leg == "estatica" and legenda_txt:
                vf = build_vf_estatico(w_str, h_str, legenda_txt)

            # ── Comando FFmpeg ─────────────────────────────────────────────
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1",
                "-framerate", "25",          # 25 fps para renderizar legenda corretamente
                "-i", img_path,
                "-i", aud_path,
                "-vf", vf,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-crf", "28",                # qualidade razoável (era 40 = muito ruim)
                "-r", "25",                  # taxa de saída 25 fps
                "-pix_fmt", "yuv420p",
                "-threads", CPU_CORES,
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-shortest",
                "-movflags", "+faststart",
                out_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)

        if result.returncode != 0:
            return f"Erro FFmpeg:\n{result.stderr[-2000:]}", 500

        @after_this_request
        def _cleanup(response):
            try:
                os.unlink(out_path)
            except Exception:
                pass
            return response

        return send_file(
            out_path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name="tron_clipe.mp4"
        )

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
