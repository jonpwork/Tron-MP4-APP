from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback
import multiprocessing, json, textwrap
import requests as http_requests

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CPU_CORES    = str(multiprocessing.cpu_count())
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR    = os.path.join(BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

# Detecta fonte disponível (Autumn > Anton > Impact fallback)
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

# ─────────────────────────────────────────────
#  PÁGINAS
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

@app.route("/status")
def status():
    return jsonify({
        "groq":      bool(GROQ_API_KEY),
        "font_name": FONT_NAME,
        "font_ok":   bool(FONT_PATH),
    })

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
# Mapa de extensão → MIME type aceito pelo Groq Whisper
_MIME_MAP = {
    ".mp3":  "audio/mpeg",
    ".mp4":  "audio/mp4",
    ".m4a":  "audio/mp4",
    ".wav":  "audio/wav",
    ".webm": "audio/webm",
    ".ogg":  "audio/ogg",    # ← áudios do WhatsApp
    ".opus": "audio/ogg",    # ← variante .opus
    ".oga":  "audio/ogg",
    ".flac": "audio/flac",
}

def _mime_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return _MIME_MAP.get(ext, "audio/mpeg")  # fallback seguro

def _groq_transcrever(audio_bytes, filename):
    """Retorna (texto, segmentos, palavras) com timestamps do Groq.
    Detecta automaticamente o MIME correto — suporta OGG/Opus do WhatsApp.
    """
    mime = _mime_for(filename)

    # OGG/Opus: Groq exige que o filename tenha extensão reconhecível
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
    texto = data.get("text") or ""
    texto = texto.strip()

    # Groq pode retornar None em vez de [] — tratamos com 'or []'
    segs = [
        {
            "start": float(s.get("start", 0)),
            "end":   float(s.get("end",   0)),
            "text":  (s.get("text") or "").strip()
        }
        for s in (data.get("segments") or [])
    ]

    palavras = [
        {
            "word":  (w.get("word") or "").strip(),
            "start": float(w.get("start", 0)),
            "end":   float(w.get("end",   0)),
        }
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

def gerar_ass(dados: list, w: int, h: int, modo_dados: str = "segmentos") -> str:
    """
    modo_dados='palavras' → TikTok karaoke (\\k timing por palavra)
    modo_dados='segmentos' → legenda por bloco (fallback)
    """
    font_size = int(w * 0.074)
    margin_v  = int(h * 0.08)

    c_branco   = "&H00FFFFFF"
    c_apagado  = "&H88AAAAAA"
    c_contorno = "&H00000000"
    c_caixa    = "&HAA000000"

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
    PALAVRAS_POR_GRUPO = 4

    if modo_dados == "palavras" and dados:
        grupos = [dados[i: i + PALAVRAS_POR_GRUPO] for i in range(0, len(dados), PALAVRAS_POR_GRUPO)]
        for grupo in grupos:
            if not grupo:
                continue
            start = grupo[0]["start"]
            end   = grupo[-1]["end"]
            if end <= start:
                end = start + 0.5
            partes = []
            for pw in grupo:
                dur_cs = max(1, int(round((pw["end"] - pw["start"]) * 100)))
                txt_w  = pw["word"].strip().replace("{","").replace("}","").replace("\\","")
                partes.append(f"{{\\k{dur_cs}}}{txt_w}")
            lines.append(f"Dialogue: 0,{_ts_ass(start)},{_ts_ass(end)},Tron,,0,0,0,,{' '.join(partes)}")
    else:
        import textwrap as _tw
        for seg in dados:
            txt = seg.get("text","").strip()
            if not txt:
                continue
            if len(txt) > 35:
                txt = _tw.fill(txt, width=35, max_lines=2, placeholder="...").replace("\n","\\N")
            txt = txt.replace("{","").replace("}","")
            lines.append(f"Dialogue: 0,{_ts_ass(seg['start'])},{_ts_ass(seg['end'])},Tron,,0,0,0,,{txt}")

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

    # Extensões suportadas — OGG/Opus do WhatsApp incluídos
    _EXT_SAFE = {".mp3", ".mp4", ".m4a", ".wav", ".webm",
                 ".ogg", ".opus", ".oga", ".flac"}

    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_ext  = os.path.splitext(img_file.filename)[1] or ".jpg"
            raw_ext  = os.path.splitext(aud_file.filename)[1].lower()
            aud_ext  = raw_ext if raw_ext in _EXT_SAFE else ".mp3"
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

            if modo_leg == "auto":
                dados_ass  = None
                modo_dados = "segmentos"

                # Tenta palavras primeiro (TikTok karaoke)
                if palavras_json:
                    try:
                        p = json.loads(palavras_json)
                        if p:
                            dados_ass  = p
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
                        fonts_dir = os.path.join(BASE_DIR, "fonts")
                        if os.path.isdir(fonts_dir):
                            vf = f"{scale_vf},ass={ass_path}:fontsdir={fonts_dir}"
                        else:
                            vf = f"{scale_vf},ass={ass_path}"
                    except Exception:
                        ass_path = None

            if ass_path is None and modo_leg == "estatica" and legenda_txt:
                vf = build_vf_estatico(w_str, h_str, legenda_txt)

            cmd = [
                "ffmpeg", "-y",
                # Foto parada: 1 fps economiza RAM e processa mais rápido
                "-loop", "1",
                "-framerate", "1",
                "-i", img_path,
                "-i", aud_path,
                "-vf", vf,
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "libx264",
                # ultrafast = menor uso de RAM e CPU
                "-preset", "ultrafast",
                # stillimage = otimizado para foto parada
                "-tune", "stillimage",
                # CRF 35 = arquivo menor, qualidade suficiente para foto parada
                "-crf", "35",
                # 1 fps — foto parada não precisa de mais
                "-r", "1",
                "-pix_fmt", "yuv420p",
                # Todos os cores trabalhando juntos
                "-threads", CPU_CORES,
                # rc-lookahead=0 e ref=1 = economiza RAM significativamente
                "-x264-params", "rc-lookahead=0:ref=1:bframes=0:weightp=0",
                "-c:a", "aac",
                "-b:a", "96k",
                "-ar", "44100",
                "-shortest",
                "-movflags", "+faststart",
                out_path,
            ]

            # Grava stderr em arquivo para não estourar RAM no Render
            log_fd, log_path = tempfile.mkstemp(suffix=".log")
            os.close(log_fd)
            try:
                with open(log_path, "w") as log_f:
                    result = subprocess.run(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=log_f,
                        timeout=1200
                    )
                if result.returncode != 0:
                    with open(log_path, "r", errors="replace") as lf:
                        log_full = lf.read()
                    # Pega as últimas linhas que contêm "Error" ou o final do log
                    lines = log_full.splitlines()
                    err_lines = [l for l in lines if any(k in l for k in ("Error","error","Invalid","No such","failed","Cannot","moov","muxer"))]
                    err_summary = "\n".join(err_lines[-30:]) if err_lines else "\n".join(lines[-30:])
                    return f"Erro FFmpeg:\n{err_summary}", 500
            finally:
                try: os.unlink(log_path)
                except Exception: pass

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
        
