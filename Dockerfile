# Usa uma imagem oficial e leve do Python
FROM python:3.10-slim

# Essa é a cura: Instala o FFmpeg diretamente no sistema!
RUN apt-get update && apt-get install -y ffmpeg

# Define a pasta de trabalho da nossa aplicação
WORKDIR /app

# Copia e instala as dependências (já vi que você usa o requirements-1.txt)
COPY requirements-1.txt .
RUN pip install --no-cache-dir -r requirements-1.txt

# Copia todo o resto do seu projeto para dentro do contêiner
COPY . .

# Libera a porta que vamos usar
EXPOSE 10000

# Inicia o app usando o Gunicorn
# IMPORTANTE: Se o seu arquivo principal de código NÃO se chamar 'app.py', 
# troque o "app:app" abaixo por "nome_do_seu_arquivo:app".
CMD ["gunicorn", "-b", "0.0.0.0:10000", "--threads", "4", "app:app"]
