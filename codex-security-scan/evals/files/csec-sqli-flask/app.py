"""SYNTHETIC EVAL FIXTURE — planted vulnerabilities. Do not deploy."""
import sqlite3

from flask import Flask, jsonify, request

app = Flask(__name__)
DB = "reports.db"


def connect():
    return sqlite3.connect(DB)


@app.route("/customers/search")
def search_customers():
    # Analyst-supplied search term goes straight into the SQL text.
    term = request.args.get("q", "")
    conn = connect()
    query = f"SELECT id, name, email FROM customers WHERE name LIKE '%{term}%'"
    rows = conn.execute(query).fetchall()
    return jsonify([{"id": r[0], "name": r[1], "email": r[2]} for r in rows])


@app.route("/customers/<int:customer_id>")
def get_customer(customer_id):
    # Decoy: this one is parameterised correctly and must NOT be reported.
    conn = connect()
    row = conn.execute(
        "SELECT id, name, email FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    return jsonify({"id": row[0], "name": row[1], "email": row[2]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
