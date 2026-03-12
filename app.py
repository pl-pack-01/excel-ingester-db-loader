"""Flask application — Phase 1 skeleton."""

import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "uploads")


@app.route("/")
def index():
    return "Excel Data Ingestor is running."


@app.route("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.run(debug=True, port=5000)
