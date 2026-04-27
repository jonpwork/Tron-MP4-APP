from flask import Flask, request, send_file, after_this_request, jsonify
import subprocess, os, tempfile, traceback
import multiprocessing, json, textwrap, shutil
import requests as http_requests

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
app = Flask(__name__)

# Render free tier tem 512MB — não carrega arquivo inteiro na RAM
# Flask vai gravar em disco em chunks via stream
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB max upload

GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR     = os.path.join(BASE_DIR, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)

# Limitar threads para não estourar RAM
CPU_CORES = "2"

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
#  SALVAR UPLOAD EM DISCO (sem carregar na RAM)
# ─────────────────────────────────────────────
CHUNK = 1024 * 1024  # 1MB por vez

def salvar_stream(file_storage, dest_path):
    """Grava o upload em disco em chunks — evita RAM cheia."""
    file_storage.stream.seek(0)
    with open(dest_path, "wb") as f:
        while True:
            chunk = file_storage.stream.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)

# ─────────────────────────────────────────────
#  PÁGINAS
# ─────────────────────────────────────────────
@app.route("/")
def index():
    return open(os.path.join(BASE_DIR, "index.html"), encoding="utf-8").read()

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

@app.route("/status")
def status():
    return jsonify({"groq": bool(GROQ_API_KEY), "font_name": FONT_NAME, "font_ok": bool(FONT_PATH)})

@app.route("/healthz")
def healthz():
    return "OK", 200

# ─────────────────────────────────────────────
#  TRANSCRIÇÃO
# ─────────────────────────────────────────────
@app.route("/transcrever", methods=["POST"])
def transcrever():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada."}), 400
    aud = request.files.get("audio")
    if not aud:
        return jsonify({"erro": "Nenhum áudio enviado."}), 400
    try:
        # Lê em chunks para não estourar RAM
        buf = bytearray()
        aud.stream.seek(0)
        while True:
            chunk = aud.stream.read(CHUNK)
            if not chunk: break
            buf.extend(chunk)
            if len(buf) > 25 * 1024 * 1024:  # Groq aceita até 25MB
                break

        resp = http_requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": (aud.filename or "audio.mp3", bytes(buf), "audio/mpeg")},
            data={
                "model": "whisper-large-v3-turbo",
                "language": "pt",
                "response_format": "verbose_json",
                "timestamp_granularities[]": "word",
            },
            timeout=300
        )
        del buf  # libera RAM imediatamente

        if resp.status_code != 200:
            raise Exception(f"Groq {resp.status_code}: {resp.text}")

        data     = resp.json()
        texto    = data.get("text", "").strip()
        segs     = [{"start": float(s.get("start",0)), "end": float(s.get("end",0)), "text": s.get("text","").strip()} for s in (data.get("segments") or [])]
        palavras = [{"word": w.get("word","").strip(), "start": float(w.get("start",0)), "end": float(w.get("end",0))} for w in (data.get("words") or []) if w.get("word","").strip()]
        return jsonify({"texto": texto, "segmentos": segs, "palavras": palavras})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ─────────────────────────────────────────────
#  GERADOR DE PROMPT DE IMAGEM
# ─────────────────────────────────────────────
@app.route("/gerar-prompt", methods=["POST"])
def gerar_prompt():
    if not GROQ_API_KEY:
        return jsonify({"erro": "GROQ_API_KEY não configurada."}), 400
    data = request.json
    transcricao = (data or {}).get("texto", "").strip()
    if not transcricao:
        return jsonify({"erro": "Texto vazio."}), 400
    try:
        resp = http_requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama3-8b-8192",
                "messages": [
                    {"role": "system", "content": "You are an expert at creating image generation prompts. Given a text in Portuguese, create a detailed and creative prompt in English to generate a YouTube/TikTok video cover image. Reply ONLY with the prompt, no explanations, no quotes."},
                    {"role": "user", "content": transcricao}
                ],
                "max_tokens": 300,
                "temperature": 0.85
            },
            timeout=30
        )
        if resp.status_code != 200:
            return jsonify({"erro": f"Groq: {resp.text}"}), 500
        prompt = resp.json()["choices"][0]["message"]["content"].strip()
        return jsonify({"prompt": prompt})
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# ─────────────────────────────────────────────
#  ASS GENERATOR
# ─────────────────────────────────────────────
def _ts(s):
    h=int(s//3600); m=int((s%3600)//60); sc=int(s%60); cs=int(round((s-int(s))*100))
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"

def gerar_ass(dados, w, h, modo="segmentos"):
    fs=int(w*0.074); mv=int(h*0.08)
    hdr=f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Tron,{FONT_NAME},{fs},&H00FFFFFF,&H88AAAAAA,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,3,6,2,2,40,40,{mv},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines=[]
    if modo=="palavras" and dados:
        grupos=[dados[i:i+4] for i in range(0,len(dados),4)]
        for g in grupos:
            if not g: continue
            s=g[0]["start"]; e=g[-1]["end"]
            if e<=s: e=s+0.5
            partes=[]
            for p in g:
                dc=max(1,int(round((p["end"]-p["start"])*100)))
                tw=p["word"].strip().replace("{","").replace("}","").replace("\\","")
                partes.append(f"{{\\k{dc}}}{tw}")
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},Tron,,0,0,0,,{' '.join(partes)}")
    else:
        for seg in dados:
            txt=seg.get("text","").strip()
            if not txt: continue
            if len(txt)>35:
                txt=textwrap.fill(txt,width=35,max_lines=2,placeholder="...").replace("\n","\\N")
            txt=txt.replace("{","").replace("}","")
            lines.append(f"Dialogue: 0,{_ts(seg['start'])},{_ts(seg['end'])},Tron,,0,0,0,,{txt}")
    return hdr+"\n".join(lines)

def _esc(t):
    return t.replace("\\","\\\\").replace("'","\\'").replace(":","\\:").replace("[","\\[").replace("]","\\]").replace(",","\\,")

def vf_estatico(w,h,leg):
    scale=f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
    if not leg.strip(): return scale
    txt=_esc(leg.strip())
    font=f"fontfile={FONT_PATH}" if FONT_PATH else f"font={FONT_NAME}"
    fs=int(int(w)*0.074); mb=int(int(h)*0.06)
    dt=f"drawtext={font}:text='{txt}':fontcolor=white:fontsize={fs}:bordercolor=black:borderw=6:shadowcolor=black@0.65:shadowx=2:shadowy=3:box=1:boxcolor=black@0.40:boxborderw=14:x=(w-text_w)/2:y=h-text_h-{mb}"
    return f"{scale},{dt}"

# ─────────────────────────────────────────────
#  CONVERSOR — otimizado para 512MB RAM
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

    w_str, h_str = RESOLUTIONS.get(resolucao, ("1080","1080"))
    w, h = int(w_str), int(h_str)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            # ── Salva em disco em chunks — SEM carregar na RAM ──
            img_ext  = os.path.splitext(img_file.filename or ".jpg")[1] or ".jpg"
            aud_ext  = os.path.splitext(aud_file.filename or ".mp3")[1] or ".mp3"
            img_path = os.path.join(tmp, "img" + img_ext)
            aud_path = os.path.join(tmp, "aud" + aud_ext)

            salvar_stream(img_file, img_path)
            salvar_stream(aud_file, aud_path)

            # ── Redimensionar imagem ANTES do ffmpeg (poupa RAM no encoding) ──
            img_small = os.path.join(tmp, "img_small.jpg")
            subprocess.run([
                "ffmpeg", "-y",
                "-i", img_path,
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
                "-q:v", "3",
                img_small
            ], capture_output=True, timeout=120)
            if os.path.exists(img_small) and os.path.getsize(img_small) > 0:
                img_path = img_small  # usa versão já redimensionada

            fd, out_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

            # ── Filtro de vídeo ──
            scale_vf = "scale=iw:ih,format=yuv420p"  # já redimensionado
            vf = scale_vf
            ass_path = None

            if modo_leg == "auto":
                dados_ass = None; modo_dados = "segmentos"
                if palavras_json:
                    try:
                        pws=json.loads(palavras_json)
                        if pws: dados_ass=pws; modo_dados="palavras"
                    except: pass
                if dados_ass is None and segs_json:
                    try: dados_ass=json.loads(segs_json)
                    except: pass
                if dados_ass:
                    try:
                        ass_content=gerar_ass(dados_ass,w,h,modo_dados)
                        ass_path=os.path.join(tmp,"legenda.ass")
                        with open(ass_path,"w",encoding="utf-8") as f:
                            f.write(ass_content)
                        fd2=f":fontsdir={FONTS_DIR}" if os.path.isdir(FONTS_DIR) else ""
                        vf=f"{scale_vf},ass={ass_path}{fd2}"
                    except Exception as err:
                        app.logger.error(f"ASS erro: {err}")
                        ass_path=None

            if ass_path is None and modo_leg=="estatica" and legenda_txt:
                vf=vf_estatico(w_str,h_str,legenda_txt)

            # ── Detecta codec do áudio ──
            probe=subprocess.run([
                "ffprobe","-v","quiet","-select_streams","a:0",
                "-show_entries","stream=codec_name","-print_format","json",aud_path
            ],capture_output=True,text=True)
            codec_aud="mp3"
            try: codec_aud=json.loads(probe.stdout)["streams"][0]["codec_name"]
            except: pass
            audio_copy=(codec_aud=="aac" and modo_leg=="nenhuma" and not legenda_txt)

            cmd=[
                "ffmpeg","-y",
                # baixo consumo de memória
                "-probesize","32M",
                "-analyzeduration","32M",
                "-loop","1",
                "-framerate","24",
                "-i",img_path,
                "-i",aud_path,
                "-vf",vf,
                "-map","0:v","-map","1:a",
                # vídeo leve
                "-c:v","libx264",
                "-preset","ultrafast",   # mais rápido, menos RAM
                "-tune","stillimage",
                "-crf","28",             # qualidade boa mas arquivo menor
                "-r","24",
                "-g","48",
                "-pix_fmt","yuv420p",
                "-threads",CPU_CORES,
                # áudio
                "-c:a","copy" if audio_copy else "aac",
                "-b:a","96k",            # 96k é suficiente para voz/música
                "-ar","44100","-ac","2",
                # saída
                "-shortest",
                "-movflags","+faststart",
                # limites de buffer baixos para poupar RAM
                "-bufsize","4M",
                "-maxrate","2M",
                out_path,
            ]

            result=subprocess.run(cmd,capture_output=True,text=True,timeout=3600)

        if result.returncode!=0:
            app.logger.error(f"FFmpeg: {result.stderr[-3000:]}")
            return f"Erro FFmpeg:\n{result.stderr[-2000:]}", 500

        @after_this_request
        def _cleanup(response):
            try: os.unlink(out_path)
            except: pass
            return response

        return send_file(out_path,mimetype="video/mp4",as_attachment=True,download_name="tron_clipe.mp4")

    except subprocess.TimeoutExpired:
        return "Tempo limite excedido (60 min).", 504
    except Exception:
        return f"Erro interno:\n{traceback.format_exc()}", 500

@app.errorhandler(Exception)
def handle_exception(e):
    return f"<pre>{traceback.format_exc()}</pre>", 500

if __name__=="__main__":
    port=int(os.environ.get("PORT",10000))
    app.run(host="0.0.0.0",port=port)
        
