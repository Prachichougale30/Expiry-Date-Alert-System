import os
import re
import base64
import smtplib
import sqlite3
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import pytesseract
from PIL import Image
import cv2
import numpy as np

# ---------------------- Firebase ----------------------
import firebase_admin
from firebase_admin import credentials, messaging

# Initialize Firebase
cred = credentials.Certificate("shopguard-notify-firebase-adminsdk-fbsvc-21e08977b4.json")  # 🔑 your service account key
firebase_admin.initialize_app(cred)



# ---------------------- App Config ----------------------
app = Flask(__name__)
app.secret_key = "secret123"

# ✅ Increase max upload size to 16 MB
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
DB_PATH = "database.db"

# ---------------------- Database Init ----------------------
def init_db():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            email TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_name TEXT,
            mfg_date TEXT,
            exp_date TEXT,
            days_left INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ---------------------- Email Function ----------------------
def send_email(to_email, subject, body):
    sender = "prachichougale2530@gmail.com"
    password = "vsuv eedd kqih kanc"  # Gmail App Password
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(msg)
    except Exception as e:
        print("Email failed:", e)

# ---------------------- OCR Processing ----------------------
def preprocess_image(image_path):
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    gray = cv2.medianBlur(gray, 3)
    return gray

def extract_dates_from_image(image_path):
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 11, 17, 17)
    gray = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    text = pytesseract.image_to_string(gray)
    text = text.upper()
    text = text.replace("O", "0").replace("I", "1").replace("L", "1")
    text = re.sub(r"[^A-Z0-9\/\-\:\.\s]", " ", text)

    # Improved patterns
    mfg_pattern = r"(?:MFG|MFD|PKD|MANUFACTURED)[^\d]*(\d{1,2}[\s\/\-\.\_]*(?:[A-Z]{3,}|[0-9]{1,2})[\s\/\-\.\_]*\d{2,4})"
    exp_pattern = r"(?:EXP|EXPIRES|USE\s*BY|BEST\s*BEFORE)[^\d]*(\d{1,2}[\s\/\-\.\_]*(?:[A-Z]{3,}|[0-9]{1,2})[\s\/\-\.\_]*\d{2,4})"

    mfg_match = re.search(mfg_pattern, text)
    exp_match = re.search(exp_pattern, text)

    def parse_date(raw):
        if not raw:
            return None
        raw = raw.strip().replace(".", "/").replace("-", "/").replace(" ", "/")
        raw = re.sub(r"/+", "/", raw)
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%b/%Y", "%d/%b/%y"):
            try:
                return datetime.strptime(raw.title(), fmt).strftime("%Y-%m-%d")
            except:
                continue
        return None

    mfg = parse_date(mfg_match.group(1)) if mfg_match else None
    exp = parse_date(exp_match.group(1)) if exp_match else None

    print("🔍 OCR TEXT:", text)
    print("✅ MFG Detected:", mfg)
    print("✅ EXP Detected:", exp)
    return mfg, exp, text

def calculate_status(exp_date):
    if not exp_date:
        return "UNKNOWN", None
    try:
        exp_date_obj = datetime.strptime(exp_date, "%Y-%m-%d").date()
    except Exception:
        return "UNKNOWN", None
    today = date.today()
    days_left = (exp_date_obj - today).days
    if days_left < 0:
        return "EXPIRED", days_left
    elif days_left <= 7:
        return "NEAR_EXPIRY", days_left
    else:
        return "VALID", days_left

# ---------------------- Routes ----------------------
@app.route("/")
def home():
    return render_template("index.html", username=session.get("username"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password, email) VALUES (?, ?, ?)",
                      (username, password, email))
            conn.commit()
            flash("Registration successful!", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Username already exists!", "danger")
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username=?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            flash("Welcome!", "success")
            return redirect(url_for("home"))
        else:
            flash("Invalid credentials", "danger")

    return render_template("login.html")


# ---------------------- Firebase Push Notification ----------------------
def send_push_notification(token, title, body):
    """Send a push notification to a single device"""
    try:
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=token,
        )
        response = messaging.send(message)
        print(f"✅ Push notification sent: {response}")
    except Exception as e:
        print(f"❌ Push notification failed: {e}")



@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully!", "info")
    return redirect(url_for("home"))

@app.route("/scan_input")
def scan_input():
    # ✅ FIX: Prevent key error if not logged in
    if "user_id" not in session:
        flash("Please login first to scan a product.", "warning")
        return redirect(url_for("login"))
    return render_template("scan_input.html", username=session.get("username"))


# ✅ NEW: Manual Entry route
@app.route("/manual_entry", methods=["GET", "POST"])
def manual_entry():
    if "user_id" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        product_name = request.form["product_name"]
        mfg_date = request.form["mfg_date"]
        exp_date = request.form["exp_date"]

        status, days_left = calculate_status(exp_date)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO products (user_id, product_name, mfg_date, exp_date, days_left)
            VALUES (?, ?, ?, ?, ?)
        """, (session["user_id"], product_name, mfg_date, exp_date, days_left))
        conn.commit()
        conn.close()

        flash("Product added manually!", "success")
        return redirect(url_for("dashboard"))

    return render_template("manual_input.html", username=session.get("username"))


@app.route("/scan_image", methods=["POST"])
def scan_image():
    if "user_id" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login"))

    file = request.files.get("image")
    if not file:
        flash("No image uploaded", "danger")
        return redirect(url_for("scan_input"))

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    mfg, exp, ocr_text = extract_dates_from_image(filepath)
    status, days_left = calculate_status(exp)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO products (user_id, product_name, mfg_date, exp_date, days_left)
        VALUES (?, ?, ?, ?, ?)
    """, (session["user_id"], filename, str(mfg), str(exp), days_left))
    conn.commit()
    conn.close()

    return render_template(
        "scan_result.html",
        image_url=url_for("static", filename=f"uploads/{filename}"),
        text=ocr_text,
        mfg=mfg,
        exp=exp,
        days=days_left,
        status=status
    )

# dasborad scan


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, product_name, mfg_date, exp_date, days_left
        FROM products
        WHERE user_id=?
    """, (session["user_id"],))
    rows = c.fetchall()
    conn.close()

    entries = []
    today = date.today()

    for row in rows:
        product_id, name, mfg, exp, days_left = row
        if days_left is None:
            status = "UNKNOWN"
        elif days_left < 0:
            status = "EXPIRED"
        elif days_left <= 7:
            status = "NEAR_EXPIRY"
        else:
            status = "VALID"

        # ✅ Email Notification: 3, 2, 1 days before expiry
        if days_left in [3, 2, 1, 0]:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT email FROM users WHERE id=?", (session["user_id"],))
            user_email = cur.fetchone()[0]
            conn.close()
            send_email(
                user_email,
                "⏰ Product Expiry Alert",
                f"Your product '{name}' will expire in {days_left} day(s). Please remove or replace it soon."
            )

        entries.append((product_id, name, mfg, exp, status, days_left))

    return render_template("dashboard.html", entries=entries)

@app.route("/delete/<int:product_id>", methods=["POST"])
def delete_product(product_id):
    if "user_id" not in session:
        flash("Please login first!", "warning")
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM products WHERE id=? AND user_id=?", (product_id, session["user_id"]))
    conn.commit()
    conn.close()

    flash("Product removed successfully!", "info")
    return redirect(url_for("dashboard"))

@app.route('/capture', methods=["POST"])
def capture():
    try:
        image_data = request.form.get("image", "")
        if not image_data:
            return jsonify({"status": "manual", "message": "No image data received."})

        image_data = image_data.split(",")[1]
        image_bytes = base64.b64decode(image_data)
        filepath = os.path.join("static/uploads", "capture.png")
        with open(filepath, "wb") as f:
            f.write(image_bytes)

        img = cv2.imread(filepath)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        text = pytesseract.image_to_string(gray)

        print("🔍 OCR Output:", text)

        # Try to detect dates
        import re
        dates = re.findall(r'(\d{1,2}\s?[A-Za-z]{3,}\s?\d{4})', text)
        print("📅 Detected dates:", dates)

        if len(dates) >= 2:
            mfg_date = dates[0]
            exp_date = dates[1]

            # Convert to proper YYYY-MM-DD
            from datetime import datetime, date
            try:
                mfg_parsed = datetime.strptime(mfg_date, "%d %b %Y").date()
                exp_parsed = datetime.strptime(exp_date, "%d %b %Y").date()
            except:
                return jsonify({"status": "manual", "message": "Date format not recognized."})

            days_left = (exp_parsed - date.today()).days
            status = "VALID" if days_left > 30 else "NEAR EXPIRY" if days_left > 0 else "EXPIRED"

            # ✅ Save to database
            conn = sqlite3.connect("database.db")
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO products (mfg_date, exp_date, status, days_left)
                VALUES (?, ?, ?, ?)
            """, (str(mfg_parsed), str(exp_parsed), status, days_left))
            conn.commit()
            conn.close()

            return jsonify({
                "status": "success",
                "mfg": str(mfg_parsed),
                "exp": str(exp_parsed),
                "state": status,
                "days": days_left
            })
        else:
            return jsonify({"status": "manual", "message": "Unable to detect expiry details."})

    except Exception as e:
        print("❌ Capture error:", e)
        return jsonify({"status": "manual", "message": str(e)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
