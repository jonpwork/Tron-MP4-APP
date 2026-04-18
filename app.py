from flask import Flask, render_template, request, send_file, jsonify
import os
import subprocess
import uuid

app = Flask(__name__)

# Pastas
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "outputs"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ================================
# ROTA PRINCIPAL (INDEX)
# ================================
@app
