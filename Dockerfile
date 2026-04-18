# Base leve e rápida
FROM python:3.11-slim

# Otimizações para logs e cache de Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Instalar ffmpeg e limpar cache para deixar a imagem menor
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Primeiro copiamos apenas os requisitos para aproveitar o cache do Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Depois copiamos todo o resto do projeto
COPY . .

# Comando para rodar com Gunicorn, usando a porta dinâmica
# Se a variável $PORT não for fornecida, ele usa 10000 como padrão
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-10000}"]
