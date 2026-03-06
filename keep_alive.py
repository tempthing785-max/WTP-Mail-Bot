# ---------- keep_alive.py ----------
from flask import Flask

app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=8080, threaded=True)
