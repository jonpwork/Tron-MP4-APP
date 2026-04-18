from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "TRON MP4 APP OK"

@app.route("/login")
def login():
    return "LOGIN OK"

if __name__ == "__main__":
    app.run()
