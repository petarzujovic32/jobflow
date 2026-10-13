# JobFlow — Job Application Tracker

JobFlow is a full-stack portfolio project for managing a job search. It uses Python, Flask and PostgreSQL, with a responsive dashboard and analytics.

## Features

- Account registration, login, logout and password hashing
- Email verification and password reset flows (SMTP configuration required)
- User profile editing and password changes
- Per-user application records with ownership checks
- Create, read, update and delete job applications
- Search, status filtering, sorting and pagination
- Dashboard counters and application-by-location/month summaries
- Analytics dashboard with Chart.js charts and conversion percentages
- CSV import/export with basic validation and duplicate skipping
- PostgreSQL schema bootstrap/migrations for fresh installs and existing tables
- Small PostgreSQL connection pool to limit open connections
- Responsive UI for desktop and mobile

## Stack

- Python 3.12+
- Flask, Flask-Login, Werkzeug
- PostgreSQL, psycopg2
- Chart.js (loaded from CDN)
- Jinja templates and responsive CSS

## Setup on Windows / VS Code

1. Install Python and PostgreSQL. Create a database named `jobflow_db` (or choose another database and update the URL).
2. Open this folder in VS Code.
3. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. Install packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

5. Copy `.env.example` to `.env` and update `DATABASE_URL` with your actual PostgreSQL username/password. Set a unique `SECRET_KEY`.
6. Run the app:

   ```powershell
   python app.py
   ```

7. Open `http://127.0.0.1:5000`.

On startup, JobFlow creates missing base tables and adds the project columns it needs. Existing tables and rows are not intentionally deleted. Back up your database before trying schema changes on valuable data.

## Email configuration

Registration requires email verification. Configure `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD` and `MAIL_FROM` in `.env`. For Gmail, use an App Password rather than your normal account password. If SMTP is not configured, the account row may be created but the verification email cannot be delivered.

## CSV format

Required headers: `Company`, `Position`.

Optional headers: `Location`, `Status`, `Applied Date`, `Notes`, `Job URL`, `Salary`, `Interview Date`.

Use `YYYY-MM-DD` for dates. JobFlow exports a matching CSV file from the dashboard.

## Run syntax check

```powershell
python -m py_compile app.py
```

## Notes before production deployment

This is a portfolio/demo application, not a production-hardened service yet. Before public deployment, add CSRF protection, rate limiting, stricter URL validation, secure cookie settings, structured logging, database migrations (e.g. Alembic), automated integration tests, and a production WSGI server. Keep `.env` private and never commit credentials.
