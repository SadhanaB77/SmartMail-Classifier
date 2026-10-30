
from flask import (
    Flask, render_template, request, session,
    redirect, url_for, flash
)
import sqlite3
import joblib
import os
import re
from PIL import Image
import pytesseract
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

app = Flask(__name__)
@app.route("/analytics")
def analytics():
    return render_template("analytics.html")
app.secret_key = "change-this-secret-key-before-deployment"

DATABASE = os.path.join(app.root_path, "database.db")

MODEL_PATH = os.path.join(
    app.root_path, "models", "email_classifier_logistic.pkl"
)
LABEL_PATH = os.path.join(
    app.root_path, "models", "label_binarizer.pkl"
)

# Windows Tesseract OCR location
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

# Check model files
if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(f"Model not found: {MODEL_PATH}")

if not os.path.exists(LABEL_PATH):
    raise FileNotFoundError(f"Label file not found: {LABEL_PATH}")

model = joblib.load(MODEL_PATH)
mlb = joblib.load(LABEL_PATH)


# Create database tables automatically
def init_db():
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject TEXT,
                category TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)


init_db()


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in {"png", "jpg", "jpeg"}
    )


# Registration page
@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get(
            "confirm_password", ""
        )

        if not email or not password or not confirm_password:
            flash("Please fill in all fields.", "error")

        elif "@" not in email or "." not in email.split("@")[-1]:
            flash("Please enter a valid email address.", "error")

        elif len(password) < 6:
            flash("Password must contain at least 6 characters.", "error")

        elif password != confirm_password:
            flash("Passwords do not match.", "error")

        else:
            hashed_password = generate_password_hash(password)

            try:
                with sqlite3.connect(DATABASE) as conn:
                    conn.execute(
                        "INSERT INTO users (email, password) VALUES (?, ?)",
                        (email, hashed_password)
                    )

                flash("Registration successful! Please log in.", "success")
                return redirect(url_for("login"))

            except sqlite3.IntegrityError:
                flash("This email is already registered.", "error")

    return render_template("register.html")


# Login page
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with sqlite3.connect(DATABASE) as conn:
            conn.row_factory = sqlite3.Row
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            ).fetchone()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            return redirect(url_for("home"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


# Logout
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Email classifier (login required)
@app.route("/", methods=["GET", "POST"])
def home():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    predictions = []
    subject = ""
    body = ""
    error = ""
    predicted = False
    extracted = False

    user_id = session["user_id"]

    if request.method == "POST":
        action = request.form.get("action", "predict")
        subject = request.form.get("subject", "").strip()
        body = request.form.get("body", "").strip()

        # Extract text from an uploaded screenshot
        if action == "extract":
            image_file = request.files.get("email_image")

            if not image_file or not image_file.filename:
                error = "Please upload an email screenshot."

            elif not allowed_file(image_file.filename):
                error = "Please upload a PNG, JPG, or JPEG image."

            else:
                try:
                    image = Image.open(
                        image_file.stream
                    ).convert("RGB")

                    text = pytesseract.image_to_string(
                        image, config="--psm 11"
                    ).strip()

                    noise_patterns = [
                        r"(?im)^.*(?:Search mail|Compose|Inbox\s*\(\d+\)).*$",
                        r"(?im)^.*(?:Starred|Snoozed|Sent|Drafts|Categories|Labels|More).*$",
                        r"(?im)^.*(?:Reply|Forward|Share in chat).*$",
                    ]

                    for pattern in noise_patterns:
                        text = re.sub(pattern, "", text)

                    text = "\n".join(
                        line.strip()
                        for line in text.splitlines()
                        if line.strip()
                    )

                    if not text:
                        error = (
                            "No text found. Upload a clearer screenshot."
                        )
                    else:
                        match = re.search(
                            r"(?im)^\s*subject\s*:\s*(.+)$", text
                        )

                        if match:
                            subject = match.group(1).strip()
                            text = (
                                text[:match.start()]
                                + text[match.end():]
                            )

                        body = text.strip()
                        extracted = True

                except Exception as exc:
                    error = f"Could not read screenshot: {exc}"

        # Predict email category
        elif action == "predict":
            if not subject and not body:
                error = "Enter a subject or body, or upload a screenshot first."

            else:
                email_text = f"Subject: {subject} Body: {body}"
                scores = model.predict_proba([email_text])[0]

                threshold = 0.25

                predictions = [
                    (str(category), float(score))
                    for category, score in zip(mlb.classes_, scores)
                    if score >= threshold
                ]

                predictions.sort(
                    key=lambda item: item[1],
                    reverse=True
                )

                predicted = True

                result_text = (
                    ", ".join(category for category, score in predictions)
                    if predictions
                    else "No category crossed the threshold"
                )

                # Save prediction for this logged-in user
                with sqlite3.connect(DATABASE) as conn:
                    conn.execute(
                        """
                        INSERT INTO predictions
                        (user_id, subject, category)
                        VALUES (?, ?, ?)
                        """,
                        (
                            user_id,
                            subject or "No subject",
                            result_text
                        )
                    )

    # Show only this user's latest five predictions
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        history = conn.execute(
            """
            SELECT subject, category
            FROM predictions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (user_id,)
        ).fetchall()

    return render_template(
        "index.html",
        predictions=predictions,
        subject=subject,
        body=body,
        error=error,
        predicted=predicted,
        extracted=extracted,
        history=history
    )


if __name__ == "__main__":
    app.run(debug=True)
