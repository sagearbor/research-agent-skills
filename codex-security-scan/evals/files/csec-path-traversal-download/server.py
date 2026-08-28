"""SYNTHETIC EVAL FIXTURE — planted vulnerabilities. Do not deploy."""
import os

from flask import Flask, request, send_file

app = Flask(__name__)
STORAGE = os.path.join(os.path.dirname(__file__), "storage")
BRAND = os.path.join(os.path.dirname(__file__), "assets")


@app.route("/download")
def download():
    # Caller names the file; the name is joined to STORAGE and served as-is.
    name = request.args.get("name", "")
    path = os.path.join(STORAGE, name)
    return send_file(path)


@app.route("/logo")
def logo():
    # Decoy: fixed filename, no caller input. Must NOT be reported.
    return send_file(os.path.join(BRAND, "logo.png"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
