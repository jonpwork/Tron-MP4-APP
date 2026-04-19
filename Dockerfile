# Usa uma imagem oficial e leve do Python
FROM python:3.10-slim

# Instala o FFmpeg diretamente no sistema
RUN apt-get update && apt-get install -y ffmpeg

# Define a pasta de trabalho da nossa aplicação
WORKDIR /app

# Copia e instala as dependências (corrigido para o nome padrão)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o resto do seu projeto para dentro do contêiner
COPY . .

# Libera a porta que vamos usar
EXPOSE 10000

# Inicia o app usando o Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:10000", "--threads", "4", "app:app"]
