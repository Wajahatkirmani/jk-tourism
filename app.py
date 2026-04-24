from flask import Flask, render_template, request, redirect, url_for, session, Response
from dotenv import load_dotenv
import psycopg2
import os
import qrcode
import uuid
from datetime import datetime
import io

load_dotenv()

app = Flask(__name__)
app.secret_key = "secret"

# -------------------------------
# DB CONNECTION
# -------------------------------
def get_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

# -------------------------------
# HOME
# -------------------------------
@app.route("/")
def home():
    return render_template("home.html")

# -------------------------------
# REGISTER
# -------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        entry_mode = request.form["entry_mode"]
        tourist_type = request.form["tourist_type"]

        origin_list = request.form.getlist("origin")
        origin = ", ".join(origin_list)

        male = int(request.form["male"])
        female = int(request.form["female"])
        children = int(request.form.get("children", 0))

        trip_id = "JK-" + str(uuid.uuid4())[:8]
        timestamp = datetime.now()

        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO entries VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (trip_id, entry_mode, tourist_type, origin, male, female, children, timestamp)
        )

        conn.commit()
        conn.close()

        return redirect(url_for("qr_page", trip_id=trip_id))

    return render_template("register.html")

# -------------------------------
# QR IMAGE (FINAL SAFE VERSION)
# -------------------------------
@app.route("/qr/<trip_id>")
def qr(trip_id):
    try:
        img = qrcode.make(trip_id)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        return Response(buffer.getvalue(), mimetype="image/png")

    except Exception as e:
        return f"QR Error: {str(e)}"

# -------------------------------
# QR PAGE (DISPLAY)
# -------------------------------
@app.route("/qr_page/<trip_id>")
def qr_page(trip_id):
    return render_template("qr.html", trip_id=trip_id)

# -------------------------------
# LOGIN
# -------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get("username").strip().lower()
        password = request.form.get("password").strip()

        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT destination FROM operators WHERE LOWER(username)=%s AND password=%s",
            (username, password)
        )

        row = cur.fetchone()
        conn.close()

        if row:
            session["destination"] = row[0]
            return redirect("/scan")
        else:
            return "Invalid login"

    return render_template("login.html")

# -------------------------------
# SCAN
# -------------------------------
@app.route("/scan", methods=["GET", "POST"])
def scan():

    if "destination" not in session:
        return redirect("/login")

    destination = session["destination"]

    if request.method == "POST":

        if request.is_json:
            data = request.get_json()
            trip_id = data.get("trip_id")
        else:
            trip_id = request.form.get("trip_id")

        if not trip_id:
            return "Invalid QR"

        conn = get_connection()
        cur = conn.cursor()

        # CHECK VALID TRIP
        cur.execute("SELECT trip_id FROM entries WHERE trip_id=%s", (trip_id,))
        exists = cur.fetchone()

        if not exists:
            conn.close()
            return render_template("scan.html", destination=destination, message="Invalid QR")

        # PREVENT DUPLICATE
        cur.execute(
            "SELECT * FROM scans WHERE trip_id=%s AND destination=%s",
            (trip_id, destination)
        )
        duplicate = cur.fetchone()

        if duplicate:
            conn.close()
            return render_template("scan.html", destination=destination, message="Already scanned")

        # INSERT SCAN
        scan_time = datetime.now()

        cur.execute(
            "INSERT INTO scans (trip_id, destination, scan_time) VALUES (%s,%s,%s)",
            (trip_id, destination, scan_time)
        )

        conn.commit()
        conn.close()

        if request.is_json:
            return {"status": "success"}

        return render_template("scan.html", destination=destination, message="Scan successful")

    return render_template("scan.html", destination=destination)

# -------------------------------
# LOGOUT
# -------------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# -------------------------------
# RUN
# -------------------------------
if __name__ == "__main__":
    app.run(debug=True)