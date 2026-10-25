"""Smart Parking: regisztracio es belepes Supabase Auth segitsegevel."""

import hmac
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from supabase import ClientOptions, create_client
from supabase_auth.errors import AuthApiError, AuthError, AuthRetryableError

load_dotenv(Path(__file__).with_name(".env"))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    SUPABASE_URL=os.getenv("SUPABASE_URL", ""),
    SUPABASE_PUBLISHABLE_KEY=os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
    AUTH_REDIRECT_URL=os.getenv("AUTH_REDIRECT_URL", "http://127.0.0.1:5000/auth/confirmed"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=16 * 1024,
)


def supabase_client():
    """Minden keres sajat klienst kap: a felhasznalok munkamenete nem keveredik."""
    if not app.config["SUPABASE_URL"] or not app.config["SUPABASE_PUBLISHABLE_KEY"]:
        raise RuntimeError("Missing Supabase configuration")
    return create_client(
        app.config["SUPABASE_URL"],
        app.config["SUPABASE_PUBLISHABLE_KEY"],
        options=ClientOptions(
            auto_refresh_token=False, persist_session=False, flow_type="implicit"
        ),
    )


def save_login(auth_session):
    # Jelszot soha nem tarolunk. A sutit a Flask titkos kulcsa alairja.
    session.clear()
    session.permanent = True
    session["access_token"] = auth_session.access_token
    session["refresh_token"] = auth_session.refresh_token


@app.context_processor
def csrf_context():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return {"csrf_token": session["csrf_token"]}


@app.before_request
def check_csrf():
    """Csak az oldalunkrol szarmazo urlapokat fogadjuk el."""
    if request.method == "POST":
        expected = session.get("csrf_token", "")
        supplied = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
            abort(400, "This form expired. Reload the page and try again.")


@app.after_request
def response_headers(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def auth_error_message(error):
    code = getattr(error, "code", "")
    if code == "email_not_confirmed":
        return "Confirm your email address before signing in. Check your inbox and spam folder."
    if code in {"over_email_send_rate_limit", "over_request_rate_limit"}:
        return "Too many attempts. Please wait before trying again."
    if code == "weak_password":
        return "Choose a stronger password with at least 8 characters."
    if code in {"email_address_not_authorized", "email_address_invalid"}:
        return "This email address cannot receive signup emails from this project. Contact the project owner."
    if code == "signup_disabled":
        return "Registration is currently unavailable. Please try again later."
    return "Check your details and try again. If you already registered, confirm your email and sign in."


def valid_email(email):
    return len(email) <= 255 and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)


def auth_page(is_register=False):
    error = None
    status = 200
    email = ""
    name = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        if not valid_email(email):
            error = "Enter a valid email address."
        elif is_register and not 1 <= len(name) <= 100:
            error = "Enter your name (up to 100 characters)."
        elif not password or (is_register and not 8 <= len(password) <= 128):
            error = "Enter a password of 8 to 128 characters." if is_register else "Enter your password."
        else:
            try:
                client = supabase_client()
                if is_register:
                    result = client.auth.sign_up({
                        "email": email,
                        "password": password,
                        "options": {
                            "data": {"full_name": name},
                            "email_redirect_to": app.config["AUTH_REDIRECT_URL"],
                        },
                    })
                    if result.session:
                        save_login(result.session)
                        return redirect(url_for("inside"))
                    flash("Check your email to confirm your account, then sign in. If you already registered, use your existing account.")
                    return redirect(url_for("index"))
                result = client.auth.sign_in_with_password({"email": email, "password": password})
                if not result.session:
                    raise AuthError("No session returned", None)
                save_login(result.session)
                return redirect(url_for("inside"))
            except (AuthRetryableError, httpx.HTTPError, RuntimeError):
                error = "We could not connect to the sign-in service. Please try again shortly."
                status = 503
            except AuthError as exc:
                error = auth_error_message(exc)
                status = 400
        if error and status == 200:
            status = 400
    return render_template("index.html", is_register=is_register, error=error, email=email, name=name), status


@app.route("/", methods=["GET", "POST"])
def index():
    return auth_page()


@app.route("/register", methods=["GET", "POST"])
def register():
    return auth_page(is_register=True)


@app.post("/resend-confirmation")
def resend_confirmation():
    email = request.form.get("email", "").strip().lower()
    if not valid_email(email):
        return render_template("index.html", is_register=False, email=email,
                               error="Enter your email address to request a new confirmation link."), 400
    try:
        supabase_client().auth.resend({
            "type": "signup", "email": email,
            "options": {"email_redirect_to": app.config["AUTH_REDIRECT_URL"]},
        })
        flash("If this account needs confirmation, a new link will be sent. Check your inbox and spam folder.")
    except (AuthRetryableError, httpx.HTTPError, RuntimeError):
        flash("We could not connect to the email service. Please try again shortly.")
    except AuthError as exc:
        flash(auth_error_message(exc))
    return redirect(url_for("index"))


@app.get("/auth/confirmed")
def confirmed():
    # Az emailes visszateres nem leptet be: uj, jelszavas belepes kell.
    # A kis script torli a Supabase altal a URL # reszebe tett tokeneket.
    flash("Sign in with your confirmed email to continue. If the link expired, enter your email and click Resend confirmation email.")
    return render_template("confirmed.html")


@app.get("/inside")
def inside():
    if not session.get("access_token") or not session.get("refresh_token"):
        return redirect(url_for("index"))
    try:
        client = supabase_client()
        try:
            user = client.auth.get_user(session["access_token"]).user
        except AuthApiError as exc:
            if exc.status != 401 and exc.code not in {"bad_jwt", "session_expired", "jwt_expired"}:
                raise
            refreshed = client.auth.refresh_session(session["refresh_token"])
            if not refreshed.session:
                raise AuthError("Session expired", None)
            save_login(refreshed.session)
            user = client.auth.get_user(refreshed.session.access_token).user
        if not user:
            raise AuthError("No authenticated user", None)
    except (AuthRetryableError, httpx.HTTPError, RuntimeError):
        return render_template("unavailable.html"), 503
    except AuthError:
        session.clear()
        flash("Your session expired. Please sign in again.")
        return redirect(url_for("index"))
    return render_template("inside.html")


@app.post("/logout")
def logout():
    try:
        if session.get("access_token") and session.get("refresh_token"):
            client = supabase_client()
            client.auth.set_session(session["access_token"], session["refresh_token"])
            client.auth.sign_out({"scope": "local"})
    except (AuthError, httpx.HTTPError, RuntimeError):
        pass
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
