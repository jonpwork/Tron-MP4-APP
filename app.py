from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback, multiprocessing, json, textwrap
import requests as http_requests

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CPU_CORES    = str(multiprocessing.cpu_count())
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR    = os.path.join(BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

_FONT_CANDIDATES = [
    (os.path.join(FONTS_DIR, "Autumn_Regular.ttf"), "Autumn"),
    (os.path.join(BASE_DIR,  "Autumn_Regular.ttf"), "Autumn"),
    (os.path.join(FONTS_DIR, "Anton-Regular.ttf"),  "Anton"),
    (os.path.join(BASE_DIR,  "Anton-Regular.ttf"),  "Anton"),
]
FONT_PATH, FONT_NAME = next(
    ((p, n) for p, n in _FONT_CANDIDATES if os.path.exists(p)),
    ("", "Impact")
)

RESOLUTIONS = {
    "720x1280":  ("720",  "1280"),
    "1080x1080": ("1080", "1080"),
    "1280x720":  ("1280", "720"),
}

@app.route("/")
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

@app.route("/status")
def status():
    return jsonify({"groq": bool(GROQ_API_KEY), "font_name": FONT_NAME, "font_ok": bool(FONT_PATH)})

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

# ── TRANSCRIÇÃO ───────────────────────────────
_MIME_MAP = {
    ".mp3": "audio/mpeg", ".mp4": "audio/mp4", ".m4a": "audio/mp4",
    ".wav": "audio/wav",  ".webm": "audio/webm", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".oga": "audio/ogg",  ".flac": "audio/flac",
}

def _mime_for(filename):
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "audio/mpeg")

def _groq_transcrever(audio_bytes, filename):
    mime = _mime_for(filename)
    safe_filename = filename if filename else "audio.mp3"
    resp = http_requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (safe_filename, audio_bytes, mime)},
        data={
            "model":                     "whisper-large-v3-turbo",
            "language":                  "pt",
            "response_format":           "verbose_json",
            "timestamp_granularities[]": "word",
        },
        timeout=120
    )
    if resp.status_code != 200:
        raise Exception(f"Groq {resp.status_code}: {resp.text}")
    data  = resp.json()
    texto = (data.get("text") or "").strip()
    segs  = [
        {"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "text": (s.get("text") or "").strip()}
        for s in (data.get("segments") or [])
    ]
    palavras = [
        {"word": (w.get("word") or "").strip(), "start": float(w.get("start", 0)), "end": float(w.get("end", 0))}
        for w in (data.get("words") or [])
        if (w.get("word") or "").strip()
    ]
    return texto, segs, palavras

@app.route("/transcrever", methods=["POST"])
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        texto, segs, palavras = _groq_transcrever(aud.read(), aud.filename or "audio.mp3")
        return jsonify({"texto": texto, "segmentos": segs, "palavras": palavras})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ── ASS ───────────────────────────────────────
def _ts_ass(s):
    h = int(s // 3600); m = int((s % 3600) // 60)
    sc = int(s % 60);   cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"

def gerar_ass(dados, w, h, modo_dados="segmentos"):
    font_size = int(w * 0.074)
    margin_v  = int(h * 0.08)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tron,{FONT_NAME},{font_size},&H00FFFFFF,&H88AAAAAA,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,3,6,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if modo_dados == "palavras" and dados:
        grupos = [dados[i:i+4] for i in range(0, len(dados), 4)]
        for g in grupos:
            if not g: continue
            start = g[0]["start"]; end = g[-1]["end"]
            if end <= start: end = start + 0.5
            partes = []
            for pw in g:
                dur_cs = max(1, int(round((pw["end"] - pw["start"]) * 100)))
                txt_w  = pw["word"].strip().replace("{","").replace("}","").replace("\\","")
                partes.append(f"{{\\k{dur_cs}}}{txt_w}")
            lines.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Tron,,0,0,0,,{' '.join(partes)}")
    else:
        for seg in dados:
            txt = seg.get("text","").strip()
            if not txt: continue
            if len(txt) > 35:
                txt = textwrap.fill(txt, width=35, max_lines=2, placeholder="...").replace("\n","\\N")
            txt = txt.replace("{","").replace("}","")
            lines.append(f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},Tron,,0,0,0,,{txt}")
    return header + "\n".join(lines)

# ── LEGENDA ESTÁTICA ──────────────────────────
def _esc(txt):
    return txt.replace("\\","\\\\").replace("'","\\'").replace(":","\\:").replace("[","\\[").replace("]","\\]").replace(",","\\,")

def build_vf_estatico(w, h, legenda):
    scale = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
    if not legenda.strip(): return scale
    txt  = _esc(legenda.strip())
    font = f"fontfile={FONT_PATH}" if os.path.exists(FONT_PATH) else "font=Impact"
    fs   = int(int(w) * 0.072)
    mb   = int(int(h) * 0.06)
    dt   = f"drawtext={font}:text='{txt}':fontcolor=white:fontsize={fs}:bordercolor=black:borderw=5:shadowcolor=black@0.65:shadowx=2:shadowy=3:box=1:boxcolor=black@0.38:boxborderw=14:x=(w-text_w)/2:y=h-text_h-{mb}"
    return f"{scale},{dt}"

# ── CONVERSOR ─────────────────────────────────
@app.route("/converter", methods=["POST"])
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

    _EXT_IMG  = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    _EXT_AUD  = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".opus", ".oga", ".flac"}

    img_path = aud_path = out_path = None
    try:
        # Lê bytes em memória antes de criar arquivos
        img_bytes = img_file.read()
        aud_bytes = aud_file.read()

        if not img_bytes:
            return "Erro: imagem vazia.", 400
        if not aud_bytes:
            return "Erro: áudio vazio.", 400

        raw_img_ext = os.path.splitext(img_file.filename or "")[1].lower()
        img_ext = raw_img_ext if raw_img_ext in _EXT_IMG else ".jpg"
        raw_aud_ext = os.path.splitext(aud_file.filename or "")[1].lower()
        aud_ext = raw_aud_ext if raw_aud_ext in _EXT_AUD else ".mp3"

        # Salva em /tmp com fdopen para garantir escrita
        img_fd, img_path = tempfile.mkstemp(suffix=img_ext, dir="/tmp")
        with os.fdopen(img_fd, "wb") as f:
            f.write(img_bytes)
        del img_bytes

        aud_fd, aud_path = tempfile.mkstemp(suffix=aud_ext, dir="/tmp")
        with os.fdopen(aud_fd, "wb") as f:
            f.write(aud_bytes)
        del aud_bytes

        out_fd, out_path = tempfile.mkstemp(suffix=".mp4", dir="/tmp")
        os.close(out_fd)

        # ── Filtro de vídeo ──
        scale_vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
        vf       = scale_vf
        ass_path = None

        if modo_leg == "auto":
            dados_ass  = None
            modo_dados = "segmentos"
            if palavras_json:
                try:
                    p = json.loads(palavras_json)
                    if p: dados_ass = p; modo_dados = "palavras"
                except Exception: pass
            if dados_ass is None and segs_json:
                try: dados_ass = json.loads(segs_json)
                except Exception: pass
            if dados_ass:
                try:
                    ass_content = gerar_ass(dados_ass, w, h, modo_dados)
                    ass_fd, ass_path = tempfile.mkstemp(suffix=".ass", dir="/tmp")
                    with os.fdopen(ass_fd, "w", encoding="utf-8") as f:
                        f.write(ass_content)
                    fonts_arg = f":fontsdir={FONTS_DIR}" if os.path.isdir(FONTS_DIR) else ""
                    vf = f"{scale_vf},ass={ass_path}{fonts_arg}"
                except Exception:
                    ass_path = None

        if ass_path is None and modo_leg == "estatica" and legenda_txt:
            vf = build_vf_estatico(w_str, h_str, legenda_txt)

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", img_path,
            "-i", aud_path,
            "-vf", vf,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "stillimage",
            "-crf", "35",
            "-r", "2",
            "-g", "2",
            "-pix_fmt", "yuv420p",
            "-threads", CPU_CORES,
            "-x264-params", "rc-lookahead=0:ref=1:bframes=0:weightp=0",
            "-c:a", "aac",
            "-b:a", "96k",
            "-ar", "44100",
            "-shortest",
            "-movflags", "+faststart",
            out_path,
        ]

        # Grava stderr em arquivo para não estourar RAM
        log_fd, log_path = tempfile.mkstemp(suffix=".log", dir="/tmp")
        os.close(log_fd)
        try:
            with open(log_path, "w") as log_f:
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=log_f, timeout=1200)
            if result.returncode != 0:
                with open(log_path, "r", errors="replace") as lf:
                    lines = lf.read().splitlines()
                err_lines = [l for l in lines if any(k in l for k in ("Error","error","Invalid","failed","Cannot","moov","No such"))]
                err_msg = "\n".join(err_lines[-20:]) if err_lines else "\n".join(lines[-20:])
                return f"Erro FFmpeg:\n{err_msg}", 500
        finally:
            try: os.unlink(log_path)
            except Exception: pass

        @after_this_request
        def _cleanup(response):
            for p in [out_path, img_path, aud_path, ass_path]:
                if p:
                    try: os.unlink(p)
                    except Exception: pass
            return response

        return send_file(out_path, mimetype="video/mp4", as_attachment=True, download_name="tron_clipe.mp4")

    except subprocess.TimeoutExpired:
        return "Tempo limite excedido (20 min).", 504
    except Exception:
        return f"Erro interno:\n{traceback.format_exc()}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

@app.route("/healthz")
def healthz():
    return "OK", 200

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500
                                                                                           
