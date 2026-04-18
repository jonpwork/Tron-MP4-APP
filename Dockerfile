FROM python:3.11-slim

# Instala FFmpeg — único motor de vídeo necessário (sem ImageMagick, sem moviepy)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia dependências primeiro (cache de layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o restante do projeto
COPY . .

# Gunicorn: 1 worker, timeout 0 = sem limite (necessário para áudios longos)
# threads 4 = permite múltiplas requisições simultâneas
CMD gunicorn app:app \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    --worker-class gthread \
    --log-level info
