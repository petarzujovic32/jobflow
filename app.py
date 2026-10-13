import os
import math
import csv
import io
import secrets
import smtplib

from datetime import datetime, timedelta

from email.message import EmailMessage

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for
)

from dotenv import load_dotenv

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)
from jobflow_utils import normalize_status, is_valid_iso_date


# =========================
# LOAD ENVIRONMENT
# =========================

load_dotenv()


# =========================
# FLASK APPLICATION
# =========================

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # CSV uploads limited to 5 MB

app.config["SECRET_KEY"] = os.getenv(
    "SECRET_KEY",
    "jobflow-development-secret-key"
)


# =========================
# FLASK LOGIN
# =========================

login_manager = LoginManager()

login_manager.init_app(app)

login_manager.login_view = "login"


# =========================
# DATABASE
# =========================

# A small connection pool prevents Flask's debug reloader and repeated
# requests from opening an unlimited number of PostgreSQL connections.
_connection_pool = None


class PooledConnection:
    """Proxy that returns a connection to the pool when close() is called."""
    def __init__(self, pool, connection):
        self._pool = pool
        self._connection = connection
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        if not self._returned:
            self._returned = True
            # A connection returned mid-transaction must not poison the next request.
            try:
                from psycopg2.extensions import TRANSACTION_STATUS_IDLE
                if self._connection.get_transaction_status() != TRANSACTION_STATUS_IDLE:
                    self._connection.rollback()
            except psycopg2.Error:
                self._pool.putconn(self._connection, close=True)
                return
            self._pool.putconn(self._connection)


def get_connection():
    global _connection_pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is missing. Copy .env.example to .env and set your PostgreSQL URL."
        )
    if _connection_pool is None:
        _connection_pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=database_url,
            connect_timeout=5,
            application_name="JobFlow",
        )
    return PooledConnection(_connection_pool, _connection_pool.getconn())


# =========================
# SEND RESET EMAIL
# =========================

def send_reset_email(
    recipient_email,
    reset_link
):

    mail_server = os.getenv(
        "MAIL_SERVER"
    )

    mail_port = int(
        os.getenv(
            "MAIL_PORT",
            "587"
        )
    )

    mail_username = os.getenv(
        "MAIL_USERNAME"
    )

    mail_password = os.getenv(
        "MAIL_PASSWORD"
    )

    mail_from = os.getenv(
        "MAIL_FROM"
    )

    message = EmailMessage()

    message["Subject"] = (
        "JobFlow - Password Reset"
    )

    message["From"] = mail_from

    message["To"] = recipient_email

    message.set_content(
        f"""
Hello,

You requested a password reset for your JobFlow account.

Click the link below to create a new password:

{reset_link}

This link is valid for 30 minutes.

If you did not request a password reset,
you can safely ignore this email.

Best regards,
JobFlow
"""
    )

    with smtplib.SMTP(
        mail_server,
        mail_port
    ) as server:

        server.starttls()

        server.login(
            mail_username,
            mail_password
        )

        server.send_message(
            message
        )


# =========================
# SEND VERIFICATION EMAIL
# =========================

def send_verification_email(
    recipient_email,
    verification_link
):

    mail_server = os.getenv(
        "MAIL_SERVER"
    )

    mail_port = int(
        os.getenv(
            "MAIL_PORT",
            "587"
        )
    )

    mail_username = os.getenv(
        "MAIL_USERNAME"
    )

    mail_password = os.getenv(
        "MAIL_PASSWORD"
    )

    mail_from = os.getenv(
        "MAIL_FROM"
    )

    message = EmailMessage()

    message["Subject"] = (
        "JobFlow - Verify Your Email"
    )

    message["From"] = mail_from

    message["To"] = recipient_email

    message.set_content(
        f"""
Hello,

Thank you for creating your JobFlow account.

Please verify your email address by clicking
the link below:

{verification_link}

This verification link is valid for 30 minutes.

If you did not create a JobFlow account,
you can safely ignore this email.

Best regards,
JobFlow
"""
    )

    with smtplib.SMTP(
        mail_server,
        mail_port
    ) as server:

        server.starttls()

        server.login(
            mail_username,
            mail_password
        )

        server.send_message(
            message
        )


# =========================
# USER MODEL
# =========================

class User(UserMixin):

    def __init__(
        self,
        user_id,
        username,
        password_hash,
        email=None
    ):

        self.id = user_id

        self.username = username

        self.password_hash = password_hash

        self.email = email


# =========================
# USER LOADER
# =========================

@login_manager.user_loader
def load_user(user_id):
    """Load the logged-in user and always release database resources."""
    connection = None
    cursor = None

    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("""
            SELECT id, username, password_hash, email
            FROM users
            WHERE id = %s
        """, (user_id,))
        user = cursor.fetchone()

        if user:
            return User(user[0], user[1], user[2], user[3])
        return None

    except psycopg2.Error:
        app.logger.exception("Database error while loading user")
        return None

    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()


# =========================
# DATABASE MIGRATION
# =========================

def ensure_database_columns():

    connection = get_connection()
    cursor = connection.cursor()

    # Fresh-install schema. Existing tables and user data are preserved.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email TEXT,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id SERIAL PRIMARY KEY,
            company TEXT NOT NULL,
            position TEXT NOT NULL,
            location TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Applied',
            applied_date DATE,
            notes TEXT DEFAULT '',
            user_id INTEGER,
            job_url TEXT,
            salary TEXT,
            interview_date DATE
        )
    """)

    # -------------------------
    # JOBS - USER ID
    # -------------------------

    cursor.execute("""
        ALTER TABLE jobs
        ADD COLUMN IF NOT EXISTS user_id INTEGER
    """)

    # -------------------------
    # JOBS - JOB URL
    # -------------------------

    cursor.execute("""
        ALTER TABLE jobs
        ADD COLUMN IF NOT EXISTS job_url TEXT
    """)

    # -------------------------
    # JOBS - SALARY
    # -------------------------

    cursor.execute("""
        ALTER TABLE jobs
        ADD COLUMN IF NOT EXISTS salary TEXT
    """)

    # -------------------------
    # JOBS - INTERVIEW DATE
    # -------------------------

    cursor.execute("""
        ALTER TABLE jobs
        ADD COLUMN IF NOT EXISTS interview_date DATE
    """)

    # -------------------------
    # USERS - EMAIL
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS email TEXT
    """)

    # -------------------------
    # PASSWORD RESET TOKEN
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS reset_token TEXT
    """)

    # -------------------------
    # PASSWORD RESET EXPIRATION
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS reset_token_expires TIMESTAMP
    """)

    # -------------------------
    # EMAIL VERIFICATION TOKEN
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS verification_token TEXT
    """)

    # -------------------------
    # EMAIL VERIFICATION EXPIRATION
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS verification_token_expires TIMESTAMP
    """)

    # -------------------------
    # EMAIL VERIFIED
    # -------------------------

    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE
    """)

    connection.commit()
    cursor.close()
    connection.close()


ensure_database_columns()


# =========================
# REGISTER
# =========================

@app.route(
    "/register",
    methods=["GET", "POST"]
)
def register():

    if current_user.is_authenticated:

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        username = request.form[
            "username"
        ].strip()

        email = request.form[
            "email"
        ].strip().lower()

        password = request.form[
            "password"
        ]

        if not username or not email or not password:

            return render_template(
                "register.html",
                error=(
                    "Username, email and password "
                    "are required."
                )
            )

        if len(username) < 3:

            return render_template(
                "register.html",
                error=(
                    "Username must contain "
                    "at least 3 characters."
                )
            )

        if len(username) > 100:

            return render_template(
                "register.html",
                error=(
                    "Username cannot contain "
                    "more than 100 characters."
                )
            )

        if "@" not in email or "." not in email:

            return render_template(
                "register.html",
                error=(
                    "Please enter a valid email address."
                )
            )

        if len(password) < 6:

            return render_template(
                "register.html",
                error=(
                    "Password must contain "
                    "at least 6 characters."
                )
            )

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id
            FROM users
            WHERE username = %s
        """, (username,))

        existing_user = cursor.fetchone()

        if existing_user:

            cursor.close()
            connection.close()

            return render_template(
                "register.html",
                error=(
                    "Username already exists."
                )
            )

        cursor.execute("""
            SELECT id
            FROM users
            WHERE LOWER(email) = %s
        """, (email,))

        existing_email = cursor.fetchone()

        if existing_email:

            cursor.close()
            connection.close()

            return render_template(
                "register.html",
                error=(
                    "This email address "
                    "is already registered."
                )
            )

        password_hash = (
            generate_password_hash(
                password
            )
        )

        verification_token = (
            secrets.token_urlsafe(32)
        )

        verification_token_expires = (
            datetime.utcnow()
            + timedelta(minutes=30)
        )

        cursor.execute("""
            INSERT INTO users (
                username,
                email,
                password_hash,
                verification_token,
                verification_token_expires,
                email_verified
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                FALSE
            )
            RETURNING id
        """, (
            username,
            email,
            password_hash,
            verification_token,
            verification_token_expires
        ))

        user_id = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM users
        """)

        user_count = cursor.fetchone()[0]

        if user_count == 1:

            cursor.execute("""
                UPDATE jobs
                SET user_id = %s
                WHERE user_id IS NULL
            """, (user_id,))

        connection.commit()

        verification_link = url_for(
            "verify_email",
            token=verification_token,
            _external=True
        )

        cursor.close()
        connection.close()

        try:

            send_verification_email(
                email,
                verification_link
            )

        except Exception as e:

            print(
                "VERIFICATION EMAIL ERROR:",
                repr(e)
            )

            return render_template(
                "register.html",
                error=(
                    "Account was created, but "
                    "we could not send the verification "
                    "email. Please check your email "
                    "settings."
                )
            )

        return render_template(
            "verification_sent.html",
            email=email
        )

    return render_template(
        "register.html"
    )


# =========================
# VERIFY EMAIL
# =========================

@app.route(
    "/verify-email/<token>"
)
def verify_email(token):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            username,
            email_verified,
            verification_token_expires
        FROM users
        WHERE verification_token = %s
    """, (token,))

    user = cursor.fetchone()

    if not user:

        cursor.close()
        connection.close()

        return render_template(
            "verify_email.html",
            error=(
                "Invalid or expired "
                "email verification link."
            )
        )

    user_id = user[0]
    already_verified = user[2]
    expires = user[3]

    if already_verified:

        cursor.close()
        connection.close()

        return render_template(
            "verify_email.html",
            success=(
                "Your email address is "
                "already verified."
            )
        )

    if (
        expires is None
        or expires < datetime.utcnow()
    ):

        cursor.execute("""
            UPDATE users
            SET
                verification_token = NULL,
                verification_token_expires = NULL
            WHERE id = %s
        """, (user_id,))

        connection.commit()

        cursor.close()
        connection.close()

        return render_template(
            "verify_email.html",
            error=(
                "This email verification link "
                "has expired."
            )
        )

    cursor.execute("""
        UPDATE users
        SET
            email_verified = TRUE,
            verification_token = NULL,
            verification_token_expires = NULL
        WHERE id = %s
    """, (user_id,))

    connection.commit()

    cursor.close()
    connection.close()

    return render_template(
        "verify_email.html",
        success=(
            "Your email address has been "
            "verified successfully."
        )
    )


# =========================
# RESEND VERIFICATION EMAIL
# =========================

def resend_verification_email(user):

    verification_token = (
        secrets.token_urlsafe(32)
    )

    verification_expires = (
        datetime.utcnow()
        + timedelta(minutes=30)
    )

    verification_link = url_for(
        "verify_email",
        token=verification_token,
        _external=True
    )

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        UPDATE users
        SET
            verification_token = %s,
            verification_token_expires = %s
        WHERE id = %s
    """, (
        verification_token,
        verification_expires,
        user.id
    ))

    connection.commit()

    cursor.close()
    connection.close()

    send_verification_email(
        user.email,
        verification_link
    )


# =========================
# RESEND VERIFICATION ROUTE
# =========================

@app.route(
    "/resend-verification",
    methods=["GET", "POST"]
)
def resend_verification():

    if current_user.is_authenticated:

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        email = request.form[
            "email"
        ].strip().lower()

        connection = get_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                username,
                password_hash,
                email,
                email_verified
            FROM users
            WHERE LOWER(email) = %s
        """, (email,))

        user_data = cursor.fetchone()

        cursor.close()
        connection.close()

        if not user_data:

            return render_template(
                "resend_verification.html",
                error=(
                    "No account was found "
                    "with that email address."
                )
            )

        user = User(
            user_data[0],
            user_data[1],
            user_data[2],
            user_data[3]
        )

        if user_data[4]:

            return render_template(
                "resend_verification.html",
                message=(
                    "This email address is already "
                    "verified. You can log in."
                )
            )

        try:

            resend_verification_email(
                user
            )

        except Exception as e:

            print(
                "RESEND VERIFICATION EMAIL ERROR:",
                repr(e)
            )

            return render_template(
                "resend_verification.html",
                error=(
                    "We could not send the "
                    "verification email. "
                    "Please try again."
                )
            )

        return render_template(
            "verification_sent.html",
            email=user.email
        )

    return render_template(
        "resend_verification.html"
    )


# =========================
# LOGIN
# =========================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if current_user.is_authenticated:

        return redirect(
            url_for("home")
        )

    if request.method == "POST":

        username = request.form[
            "username"
        ].strip()

        password = request.form[
            "password"
        ]

        connection = get_connection()

        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                id,
                username,
                password_hash,
                email,
                email_verified
            FROM users
            WHERE username = %s
        """, (username,))

        user_data = cursor.fetchone()

        cursor.close()

        connection.close()

        if user_data:

            user = User(
                user_data[0],
                user_data[1],
                user_data[2],
                user_data[3]
            )

            if check_password_hash(
                user.password_hash,
                password
            ):

                if not user_data[4]:

                    return render_template(
                        "login.html",
                        error=(
                            "Please verify your email "
                            "address before logging in."
                        )
                    )

                login_user(user)

                return redirect(
                    url_for("home")
                )

        return render_template(
            "login.html",
            error=(
                "Invalid username "
                "or password."
            )
        )

    return render_template(
        "login.html"
    )


# =========================
# LOGOUT
# =========================

@app.route("/logout")
@login_required
def logout():

    logout_user()

    return redirect(
        url_for("login")
    )


# =========================
# PROFILE
# =========================

@app.route("/profile")
@login_required
def profile():

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
    """, (current_user.id,))

    total_jobs = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Applied'
    """, (current_user.id,))

    applied_jobs = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Interview'
    """, (current_user.id,))

    interview_jobs = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Offer'
    """, (current_user.id,))

    offer_jobs = cursor.fetchone()[0]

    cursor.close()

    connection.close()

    return render_template(
        "profile.html",
        total_jobs=total_jobs,
        applied_jobs=applied_jobs,
        interview_jobs=interview_jobs,
        offer_jobs=offer_jobs
    )


# =========================
# EDIT PROFILE
# =========================

@app.route(
    "/edit-profile",
    methods=["GET", "POST"]
)
@login_required
def edit_profile():

    error = None
    success = None

    if request.method == "POST":

        new_username = request.form[
            "username"
        ].strip()

        new_email = request.form[
            "email"
        ].strip().lower()

        if not new_username:

            error = (
                "Username cannot be empty."
            )

        elif len(new_username) < 3:

            error = (
                "Username must contain "
                "at least 3 characters."
            )

        elif len(new_username) > 100:

            error = (
                "Username cannot contain "
                "more than 100 characters."
            )

        elif not new_email:

            error = (
                "Email cannot be empty."
            )

        elif "@" not in new_email or "." not in new_email:

            error = (
                "Please enter a valid email address."
            )

        else:

            connection = get_connection()

            cursor = connection.cursor()

            cursor.execute("""
                SELECT id
                FROM users
                WHERE username = %s
                AND id != %s
            """, (
                new_username,
                current_user.id
            ))

            existing_user = cursor.fetchone()

            if existing_user:

                error = (
                    "This username is already taken."
                )

            else:

                cursor.execute("""
                    SELECT id
                    FROM users
                    WHERE LOWER(email) = %s
                    AND id != %s
                """, (
                    new_email,
                    current_user.id
                ))

                existing_email = cursor.fetchone()

                if existing_email:

                    error = (
                        "This email address "
                        "is already registered."
                    )

                else:

                    cursor.execute("""
                        UPDATE users
                        SET
                            username = %s,
                            email = %s
                        WHERE id = %s
                    """, (
                        new_username,
                        new_email,
                        current_user.id
                    ))

                    connection.commit()

                    current_user.username = (
                        new_username
                    )

                    current_user.email = (
                        new_email
                    )

                    success = (
                        "Profile updated successfully."
                    )

            cursor.close()
            connection.close()

    return render_template(
        "edit_profile.html",
        error=error,
        success=success
    )


# =========================
# FORGOT PASSWORD
# =========================

@app.route(
    "/forgot-password",
    methods=["GET", "POST"]
)
def forgot_password():

    error = None
    success = None
    reset_link = None

    if request.method == "POST":

        username = request.form[
            "username"
        ].strip()

        if not username:

            error = (
                "Please enter your username."
            )

        else:

            connection = get_connection()
            cursor = connection.cursor()

            cursor.execute("""
                SELECT
                    id,
                    email
                FROM users
                WHERE username = %s
            """, (username,))

            user = cursor.fetchone()

            if user:

                user_id = user[0]
                user_email = user[1]

                if not user_email:

                    error = (
                        "This account does not have "
                        "an email address."
                    )

                    cursor.close()
                    connection.close()

                    return render_template(
                        "forgot_password.html",
                        error=error,
                        reset_link=None
                    )

                reset_token = secrets.token_urlsafe(32)

                reset_token_expires = (
                    datetime.utcnow()
                    + timedelta(minutes=30)
                )

                cursor.execute("""
                    UPDATE users
                    SET
                        reset_token = %s,
                        reset_token_expires = %s
                    WHERE id = %s
                """, (
                    reset_token,
                    reset_token_expires,
                    user_id
                ))

                connection.commit()

                reset_link = url_for(
                    "reset_password",
                    token=reset_token,
                    _external=True
                )

                cursor.close()
                connection.close()

                try:

                    send_reset_email(
                        user_email,
                        reset_link
                    )

                    success = (
                        "Password reset link has "
                        "been sent to your email address."
                    )

                except Exception as e:

                    print(
                        "EMAIL ERROR:",
                        repr(e)
                    )

                    error = (
                        "Unable to send the password "
                        "reset email. The reset link "
                        "was generated, but email "
                        "sending failed."
                    )

            else:

                success = (
                    "If an account with that username "
                    "exists, a password reset email "
                    "has been requested."
                )

                cursor.close()
                connection.close()

    return render_template(
        "forgot_password.html",
        error=error,
        success=success,
        reset_link=reset_link
    )


# =========================
# RESET PASSWORD
# =========================

@app.route(
    "/reset-password/<token>",
    methods=["GET", "POST"]
)
def reset_password(token):

    error = None

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            username,
            reset_token_expires
        FROM users
        WHERE reset_token = %s
    """, (token,))

    user = cursor.fetchone()

    if not user:

        cursor.close()

        connection.close()

        return render_template(
            "reset_password.html",
            error=(
                "Invalid or expired "
                "password reset link."
            )
        )

    if (
        user[2] is None
        or user[2] < datetime.utcnow()
    ):

        cursor.execute("""
            UPDATE users
            SET
                reset_token = NULL,
                reset_token_expires = NULL
            WHERE id = %s
        """, (user[0],))

        connection.commit()

        cursor.close()

        connection.close()

        return render_template(
            "reset_password.html",
            error=(
                "This password reset link "
                "has expired."
            )
        )

    if request.method == "POST":

        new_password = request.form[
            "new_password"
        ]

        confirm_password = request.form[
            "confirm_password"
        ]

        if len(new_password) < 6:

            error = (
                "Password must contain "
                "at least 6 characters."
            )

        elif new_password != confirm_password:

            error = (
                "Passwords do not match."
            )

        else:

            new_password_hash = (
                generate_password_hash(
                    new_password
                )
            )

            cursor.execute("""
                UPDATE users
                SET
                    password_hash = %s,
                    reset_token = NULL,
                    reset_token_expires = NULL
                WHERE id = %s
            """, (
                new_password_hash,
                user[0]
            ))

            connection.commit()

            cursor.close()

            connection.close()

            return redirect(
                url_for("login")
            )

    cursor.close()

    connection.close()

    return render_template(
        "reset_password.html",
        error=error,
        username=user[1]
    )


# =========================
# CHANGE PASSWORD
# =========================

@app.route(
    "/change-password",
    methods=["GET", "POST"]
)
@login_required
def change_password():

    error = None

    success = None

    if request.method == "POST":

        current_password = request.form[
            "current_password"
        ]

        new_password = request.form[
            "new_password"
        ]

        confirm_password = request.form[
            "confirm_password"
        ]

        if not check_password_hash(
            current_user.password_hash,
            current_password
        ):

            error = (
                "Current password "
                "is incorrect."
            )

        elif len(new_password) < 6:

            error = (
                "New password must contain "
                "at least 6 characters."
            )

        elif new_password != confirm_password:

            error = (
                "New passwords "
                "do not match."
            )

        else:

            connection = get_connection()

            cursor = connection.cursor()

            new_password_hash = (
                generate_password_hash(
                    new_password
                )
            )

            cursor.execute("""
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
            """, (
                new_password_hash,
                current_user.id
            ))

            connection.commit()

            cursor.close()

            connection.close()

            current_user.password_hash = (
                new_password_hash
            )

            success = (
                "Password changed "
                "successfully."
            )

    return render_template(
        "change_password.html",
        error=error,
        success=success
    )


# =========================
# ADD JOB APPLICATION
# =========================

@app.route("/add", methods=["POST"])
@login_required
def add_job():
    company = request.form.get("company", "").strip()
    position = request.form.get("position", "").strip()
    location = request.form.get("location", "").strip()
    status = request.form.get("status", "Applied").strip()
    applied_date = request.form.get("applied_date", "").strip() or None
    notes = request.form.get("notes", "").strip()
    job_url = request.form.get("job_url", "").strip() or None
    salary = request.form.get("salary", "").strip() or None
    interview_date = request.form.get("interview_date", "").strip() or None
    if not company or not position:
        return redirect(url_for("home", error="Company and position are required."))
    status = normalize_status(status)
    if not is_valid_iso_date(applied_date) or not is_valid_iso_date(interview_date):
        return redirect(url_for("home", error="Please enter dates in YYYY-MM-DD format."))

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO jobs (
                company, position, location, status, applied_date, notes,
                job_url, salary, interview_date, user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            company, position, location, status, applied_date, notes,
            job_url, salary, interview_date, current_user.id
        ))
        connection.commit()
    except Exception:
        connection.rollback()
        app.logger.exception("Could not add job application")
        return redirect(url_for("home", error="Could not save this application. Please check the fields."))
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("home", added="1"))


# =========================
# HOME / DASHBOARD
# =========================

@app.route("/")
@login_required
def home():

    connection = get_connection()

    cursor = connection.cursor()

    search = request.args.get(
        "search",
        ""
    ).strip()

    status_filter = request.args.get(
        "status",
        ""
    ).strip()

    sort = request.args.get(
        "sort",
        "newest"
    ).strip()

    try:

        page = int(
            request.args.get(
                "page",
                1
            )
        )

    except ValueError:

        page = 1

    if page < 1:

        page = 1

    per_page = 10

    offset = (
        page - 1
    ) * per_page

    where_clause = """
        FROM jobs
        WHERE user_id = %s
    """

    params = [
        current_user.id
    ]

    if search:

        where_clause += """
            AND (
                company ILIKE %s
                OR position ILIKE %s
            )
        """

        search_term = (
            f"%{search}%"
        )

        params.extend([
            search_term,
            search_term
        ])

    if status_filter:

        where_clause += """
            AND status = %s
        """

        params.append(
            status_filter
        )

    count_query = (
        "SELECT COUNT(*) "
        + where_clause
    )

    cursor.execute(
        count_query,
        params
    )

    filtered_jobs = (
        cursor.fetchone()[0]
    )

    total_pages = max(
        1,
        math.ceil(
            filtered_jobs
            / per_page
        )
    )

    if page > total_pages:

        page = total_pages

        offset = (
            page - 1
        ) * per_page

    query = """
        SELECT
            id,
            company,
            position,
            location,
            status,
            applied_date,
            notes,
            job_url,
            salary,
            interview_date
    """ + where_clause

    if sort == "oldest":

        query += """
            ORDER BY
                applied_date ASC
                NULLS LAST
        """

    elif sort == "company_asc":

        query += """
            ORDER BY company ASC
        """

    elif sort == "company_desc":

        query += """
            ORDER BY company DESC
        """

    else:

        query += """
            ORDER BY
                applied_date DESC
                NULLS LAST
        """

    query += """
        LIMIT %s OFFSET %s
    """

    query_params = (
        params
        + [
            per_page,
            offset
        ]
    )

    cursor.execute(
        query,
        query_params
    )

    jobs = cursor.fetchall()

    # -------------------------
    # STATISTICS
    # -------------------------

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
    """, (current_user.id,))

    total_jobs = (
        cursor.fetchone()[0]
    )

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Applied'
    """, (current_user.id,))

    applied_jobs = (
        cursor.fetchone()[0]
    )

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Interview'
    """, (current_user.id,))

    interview_jobs = (
        cursor.fetchone()[0]
    )

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Offer'
    """, (current_user.id,))

    offer_jobs = (
        cursor.fetchone()[0]
    )

    cursor.execute("""
        SELECT COUNT(*)
        FROM jobs
        WHERE user_id = %s
        AND status = 'Rejected'
    """, (current_user.id,))

    rejected_jobs = (
        cursor.fetchone()[0]
    )

    # -------------------------
    # APPLICATIONS BY LOCATION
    # -------------------------

    cursor.execute("""
        SELECT
            COALESCE(
                NULLIF(TRIM(location), ''),
                'Not specified'
            ) AS location_name,
            COUNT(*) AS application_count
        FROM jobs
        WHERE user_id = %s
        GROUP BY location_name
        ORDER BY application_count DESC
    """, (current_user.id,))

    location_stats = cursor.fetchall()

    # -----------------------------
    # MONTHLY APPLICATIONS
    # -----------------------------

    cursor.execute("""
        SELECT
            TO_CHAR(
                applied_date,
                'YYYY-MM'
            ) AS month,
            COUNT(*) AS application_count
        FROM jobs
        WHERE user_id = %s
          AND applied_date IS NOT NULL
        GROUP BY TO_CHAR(
            applied_date,
            'YYYY-MM'
        )
        ORDER BY month ASC
    """, (current_user.id,))

    monthly_stats = cursor.fetchall()

    cursor.close()

    connection.close()

    return render_template(
        "index.html",

        jobs=jobs,

        total_jobs=total_jobs,
        applied_jobs=applied_jobs,
        interview_jobs=interview_jobs,
        offer_jobs=offer_jobs,
        rejected_jobs=rejected_jobs,

        search=search,
        status_filter=status_filter,
        sort=sort,

        page=page,
        total_pages=total_pages,
        filtered_jobs=filtered_jobs,

        location_stats=location_stats,
        monthly_stats=monthly_stats,
        page_error=request.args.get("error"),
        added=request.args.get("added") == "1",
    )
# =========================
# EDIT JOB
# =========================

@app.route(
    "/edit/<int:job_id>",
    methods=["GET", "POST"]
)
@login_required
def edit_job(job_id):

    connection = get_connection()

    cursor = connection.cursor()

    if request.method == "POST":

        company = request.form[
            "company"
        ]

        position = request.form[
            "position"
        ]

        location = request.form[
            "location"
        ]

        status = request.form[
            "status"
        ]

        applied_date = (
            request.form[
                "applied_date"
            ]
            or None
        )

        notes = request.form[
            "notes"
        ]

        job_url = (
            request.form.get(
                "job_url",
                ""
            ).strip()
            or None
        )

        salary = (
            request.form.get(
                "salary",
                ""
            ).strip()
            or None
        )

        interview_date = (
            request.form.get(
                "interview_date",
                ""
            )
            or None
        )

        cursor.execute("""
            UPDATE jobs
            SET
                company = %s,
                position = %s,
                location = %s,
                status = %s,
                applied_date = %s,
                notes = %s,
                job_url = %s,
                salary = %s,
                interview_date = %s
            WHERE id = %s
            AND user_id = %s
        """, (
            company,
            position,
            location,
            status,
            applied_date,
            notes,
            job_url,
            salary,
            interview_date,
            job_id,
            current_user.id
        ))

        connection.commit()

        cursor.close()

        connection.close()

        return redirect(
            url_for("home")
        )

    cursor.execute("""
        SELECT
            id,
            company,
            position,
            location,
            status,
            applied_date,
            notes,
            job_url,
            salary,
            interview_date
        FROM jobs
        WHERE id = %s
        AND user_id = %s
    """, (
        job_id,
        current_user.id
    ))

    job = cursor.fetchone()

    cursor.close()

    connection.close()

    if job is None:

        return (
            "Job application "
            "not found",
            404
        )

    return render_template(
        "edit.html",
        job=job
    )


# =========================
# JOB DETAILS
# =========================

@app.route(
    "/job/<int:job_id>"
)
@login_required
def job_details(job_id):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            id,
            company,
            position,
            location,
            status,
            applied_date,
            notes,
            job_url,
            salary,
            interview_date
        FROM jobs
        WHERE id = %s
        AND user_id = %s
    """, (
        job_id,
        current_user.id
    ))

    job = cursor.fetchone()

    cursor.close()

    connection.close()

    if job is None:

        return (
            "Job application "
            "not found",
            404
        )

    return render_template(
        "details.html",
        job=job
    )


# =========================
# DELETE JOB
# =========================

@app.route(
    "/delete/<int:job_id>",
    methods=["POST"]
)
@login_required
def delete_job(job_id):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute("""
        DELETE FROM jobs
        WHERE id = %s
        AND user_id = %s
    """, (
        job_id,
        current_user.id
    ))

    connection.commit()

    cursor.close()

    connection.close()

    return redirect(
        url_for("home")
    )


# =========================
# EXPORT CSV
# =========================

@app.route("/export-csv")
@login_required
def export_csv():

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT
                company,
                position,
                location,
                status,
                applied_date,
                notes,
                job_url,
                salary,
                interview_date
            FROM jobs
            WHERE user_id = %s
            ORDER BY applied_date DESC NULLS LAST
        """, (current_user.id,))

        jobs = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Company", "Position", "Location", "Status",
        "Applied Date", "Notes", "Job URL", "Salary",
        "Interview Date"
    ])
    writer.writerows(jobs)

    response = app.response_class(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8"
    )
    response.headers["Content-Disposition"] = (
        "attachment; filename=jobflow_applications.csv"
    )
    return response


# =========================
# ANALYTICS
# =========================

@app.route("/analytics")
@login_required
def analytics():

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("""
            SELECT COUNT(*)
            FROM jobs
            WHERE user_id = %s
        """, (current_user.id,))
        total_jobs = cursor.fetchone()[0]

        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'Applied'),
                COUNT(*) FILTER (WHERE status = 'Interview'),
                COUNT(*) FILTER (WHERE status = 'Offer'),
                COUNT(*) FILTER (WHERE status = 'Rejected')
            FROM jobs
            WHERE user_id = %s
        """, (current_user.id,))
        applied_jobs, interview_jobs, offer_jobs, rejected_jobs = cursor.fetchone()

        interview_percentage = round(interview_jobs / total_jobs * 100, 1) if total_jobs else 0
        offer_percentage = round(offer_jobs / total_jobs * 100, 1) if total_jobs else 0
        rejected_percentage = round(rejected_jobs / total_jobs * 100, 1) if total_jobs else 0

        cursor.execute("""
            SELECT applied_date, COUNT(*)
            FROM jobs
            WHERE user_id = %s
              AND applied_date IS NOT NULL
            GROUP BY applied_date
            ORDER BY applied_date
        """, (current_user.id,))
        date_data = cursor.fetchall()
        chart_dates = [str(row[0]) for row in date_data]
        chart_date_counts = [row[1] for row in date_data]

        cursor.execute("""
            SELECT TO_CHAR(applied_date, 'YYYY-MM') AS month, COUNT(*)
            FROM jobs
            WHERE user_id = %s
              AND applied_date IS NOT NULL
            GROUP BY TO_CHAR(applied_date, 'YYYY-MM')
            ORDER BY month
        """, (current_user.id,))
        month_data = cursor.fetchall()
        chart_months = [row[0] for row in month_data]
        chart_month_counts = [row[1] for row in month_data]

        cursor.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(location), ''), 'Not specified') AS location_name,
                COUNT(*) AS application_count
            FROM jobs
            WHERE user_id = %s
            GROUP BY COALESCE(NULLIF(TRIM(location), ''), 'Not specified')
            ORDER BY application_count DESC
            LIMIT 10
        """, (current_user.id,))
        location_data = cursor.fetchall()
        chart_location_labels = [row[0] for row in location_data]
        chart_location_counts = [row[1] for row in location_data]

        chart_status_labels = ["Applied", "Interview", "Offer", "Rejected"]
        chart_status_counts = [applied_jobs, interview_jobs, offer_jobs, rejected_jobs]

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "analytics.html",
        total_jobs=total_jobs,
        applied_jobs=applied_jobs,
        interview_jobs=interview_jobs,
        offer_jobs=offer_jobs,
        rejected_jobs=rejected_jobs,
        interview_percentage=interview_percentage,
        offer_percentage=offer_percentage,
        rejected_percentage=rejected_percentage,
        date_data=date_data,
        chart_dates=chart_dates,
        chart_date_counts=chart_date_counts,
        chart_months=chart_months,
        chart_month_counts=chart_month_counts,
        chart_location_labels=chart_location_labels,
        chart_location_counts=chart_location_counts,
        chart_status_labels=chart_status_labels,
        chart_status_counts=chart_status_counts
    )


# =========================
# IMPORT CSV
# =========================

@app.route("/import-csv", methods=["GET", "POST"])
@login_required
def import_csv():

    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            return "CSV file nije izabran.", 400

        connection = None
        cursor = None
        imported = 0
        skipped = 0

        try:
            content = file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))

            required_columns = {"Company", "Position"}
            if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
                return (
                    "CSV fajl mora da sadrži kolone Company i Position. "
                    "Najlakše je da prvo izvezeš JobFlow CSV pa njega koristiš kao šablon.",
                    400
                )

            connection = get_connection()
            cursor = connection.cursor()
            valid_statuses = {"Applied", "Interview", "Offer", "Rejected"}

            for row in reader:
                company = (row.get("Company") or "").strip()
                position = (row.get("Position") or "").strip()
                location = (row.get("Location") or "").strip()
                status = (row.get("Status") or "Applied").strip()
                applied_date = (row.get("Applied Date") or "").strip()
                notes = (row.get("Notes") or "").strip()
                job_url = (row.get("Job URL") or "").strip() or None
                salary = (row.get("Salary") or "").strip() or None
                interview_date = (row.get("Interview Date") or "").strip()

                if not company or not position:
                    skipped += 1
                    continue

                status = normalize_status(status)

                # Validate dates before using PostgreSQL date casts.
                if not is_valid_iso_date(applied_date) or not is_valid_iso_date(interview_date):
                    skipped += 1
                    continue

                cursor.execute("""
                    SELECT id
                    FROM jobs
                    WHERE user_id = %s
                      AND LOWER(TRIM(company)) = LOWER(TRIM(%s))
                      AND LOWER(TRIM(position)) = LOWER(TRIM(%s))
                      AND (
                          applied_date = NULLIF(%s, '')::date
                          OR (applied_date IS NULL AND NULLIF(%s, '') IS NULL)
                      )
                    LIMIT 1
                """, (current_user.id, company, position, applied_date, applied_date))

                if cursor.fetchone():
                    skipped += 1
                    continue

                cursor.execute("""
                    INSERT INTO jobs (
                        company, position, location, status, applied_date,
                        notes, job_url, salary, interview_date, user_id
                    )
                    VALUES (
                        %s, %s, %s, %s, NULLIF(%s, '')::date,
                        %s, %s, %s, NULLIF(%s, '')::date, %s
                    )
                """, (
                    company, position, location, status, applied_date,
                    notes, job_url, salary, interview_date, current_user.id
                ))
                imported += 1

            connection.commit()
            return (
                f"Uspješno uvezeno aplikacija: {imported}. "
                f"Preskočeno duplikata ili neispravnih redova: {skipped}. "
                f'<br><br><a href="{url_for("home")}">Nazad na JobFlow</a>'
            )

        except Exception as e:
            if connection:
                connection.rollback()
            app.logger.exception("CSV import failed")
            return "Greška prilikom importa CSV fajla. Provjeri da li CSV ima ispravne kolone i datume.", 400

        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    return render_template("import_csv.html")


# =========================
# START APPLICATION
# =========================

if __name__ == "__main__":

    app.run(
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )