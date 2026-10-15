from flask import (
    Flask, render_template, request, redirect,
    url_for, session, Response, jsonify
)
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.errors import UniqueViolation
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import qrcode
import uuid
import io

load_dotenv()

app = Flask(__name__)

# Use a secure secret from environment variables.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-this-secret")

# Keep operator sessions alive during a scanning shift.
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 8


# ---------------------------------
# DATABASE CONNECTION
# ---------------------------------
def get_connection():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not configured.")

    return psycopg2.connect(database_url, connect_timeout=15)


# ---------------------------------
# JSON RESPONSE HELPER
# ---------------------------------
def scan_response(status, message, http_status=200):
    return jsonify({
        "status": status,
        "message": message
    }), http_status


# ---------------------------------
# HOME
# ---------------------------------
@app.route("/")
def home():
    return render_template("home.html")


# ---------------------------------
# REGISTER
# ---------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        try:
            entry_mode = request.form["entry_mode"]
            tourist_type = request.form["tourist_type"]

            origin_list = request.form.getlist("origin")
            origin = ", ".join(origin_list)

            male = int(request.form["male"])
            female = int(request.form["female"])
            children = int(request.form.get("children", 0))

            if min(male, female, children) < 0:
                return "Visitor counts cannot be negative.", 400

            trip_id = "JK-" + uuid.uuid4().hex[:8].upper()
            timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO entries
                        (
                            trip_id, entry_mode, tourist_type, origin,
                            male, female, children, timestamp
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            trip_id, entry_mode, tourist_type, origin,
                            male, female, children, timestamp
                        )
                    )

            return redirect(url_for("qr_page", trip_id=trip_id))

        except (ValueError, KeyError):
            return "Invalid registration form data.", 400

        except Exception:
            app.logger.exception("Tourist registration failed.")
            return "Registration failed. Please try again.", 500

    return render_template("register.html")


# ---------------------------------
# QR IMAGE
# ---------------------------------
@app.route("/qr/<trip_id>")
def qr(trip_id):
    try:
        img = qrcode.make(trip_id)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        return Response(buffer.getvalue(), mimetype="image/png")

    except Exception:
        app.logger.exception("QR image generation failed.")
        return "Unable to generate QR code.", 500


# ---------------------------------
# QR PAGE
# ---------------------------------
@app.route("/qr_page/<trip_id>")
def qr_page(trip_id):
    return render_template("qr.html", trip_id=trip_id)


# ---------------------------------
# LOGIN
# ---------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT username, password, destination
                        FROM operators
                        WHERE LOWER(username) = %s
                        """,
                        (username,)
                    )

                    operator = cur.fetchone()

            if not operator:
                return render_template(
                    "login.html",
                    error="Invalid username or password."
                ), 401

            stored_password = operator[1]

            # Support the existing prototype's plaintext passwords.
            # Hashed passwords are also supported for future migration.
            password_valid = False

            if stored_password.startswith(("scrypt:", "pbkdf2:")):
                password_valid = check_password_hash(
                    stored_password, password
                )
            else:
                password_valid = stored_password == password

            if password_valid:
                session.clear()
                session.permanent = True
                session["operator_username"] = operator[0]
                session["destination"] = operator[2]

                return redirect(url_for("scan"))

            return render_template(
                "login.html",
                error="Invalid username or password."
            ), 401

        except Exception:
            app.logger.exception("Operator login failed.")
            return "Login service temporarily unavailable.", 500

    # If the operator already has a session, don't ask them to log in again.
    if "destination" in session:
        return redirect(url_for("scan"))

    return render_template("login.html")


# ---------------------------------
# SCAN
# ---------------------------------
@app.route("/scan", methods=["GET", "POST"])
def scan():

    # Require an authenticated operator.
    if "destination" not in session:
        if request.is_json:
            return scan_response(
                "unauthorized",
                "Your session has expired. Please log in again.",
                401
            )

        return redirect(url_for("login"))

    destination = session["destination"]

    if request.method == "GET":
        return render_template("scan.html", destination=destination)

    # Accept JSON from the camera or regular form submissions.
    if request.is_json:
        data = request.get_json(silent=True) or {}
        trip_id = data.get("trip_id")
    else:
        trip_id = request.form.get("trip_id")

    trip_id = (trip_id or "").strip()

    if not trip_id:
        if request.is_json:
            return scan_response("invalid", "QR code is empty.", 400)

        return render_template(
            "scan.html",
            destination=destination,
            message="QR code is empty."
        ), 400

    conn = None

    try:
        conn = get_connection()

        with conn:
            with conn.cursor() as cur:

                # 1. Confirm that the Trip ID exists.
                cur.execute(
                    "SELECT trip_id FROM entries WHERE trip_id = %s",
                    (trip_id,)
                )

                if cur.fetchone() is None:
                    if request.is_json:
                        return scan_response(
                            "invalid",
                            "Invalid QR code. Tourist registration not found.",
                            404
                        )

                    return render_template(
                        "scan.html",
                        destination=destination,
                        message="Invalid QR code. Tourist not registered."
                    )

                # 2. Insert the visit. The unique index protects against
                # duplicate scans even if two requests arrive together.
                try:
                    cur.execute(
                        """
                        INSERT INTO scans (trip_id, destination, scan_time)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (trip_id, destination) DO NOTHING
                        RETURNING id
                        """,
                        (
                            trip_id,
                            destination,
                            datetime.now(timezone.utc).replace(tzinfo=None)
                        )
                    )

                    inserted = cur.fetchone()

                except UniqueViolation:
                    inserted = None

                if inserted is None:
                    if request.is_json:
                        return scan_response(
                            "duplicate",
                            f"⚠️ QR already scanned at {destination}."
                        )

                    return render_template(
                        "scan.html",
                        destination=destination,
                        message=f"QR already scanned at {destination}."
                    )

        if request.is_json:
            return scan_response(
                "success",
                f"✅ Scan successful at {destination}!"
            )

        return render_template(
            "scan.html",
            destination=destination,
            message=f"Scan successful at {destination}!"
        )

    except Exception:
        app.logger.exception("Destination scan failed.")

        if request.is_json:
            return scan_response(
                "error",
                "Database error while processing the scan. Please try again.",
                500
            )

        return render_template(
            "scan.html",
            destination=destination,
            message="Database error while processing the scan."
        ), 500

    finally:
        if conn is not None:
            conn.close()


# ---------------------------------
# LOGOUT
# ---------------------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------
# RUN
# ---------------------------------
if __name__ == "__main__":
    app.run(debug=True)