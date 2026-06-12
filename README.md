# Ziva Pro

Waitlist + referral + reward platform — Flask, PostgreSQL, Resend, Tailwind.

## Stack

| Layer | Technology |
|---|---|
| Framework | Flask 3.1 |
| Database | **PostgreSQL 15** (via SQLAlchemy + psycopg2) |
| Migrations | Flask-Migrate / Alembic |
| Email | Resend |
| Auth hashing | Flask-Bcrypt |
| Rate limiting | Flask-Limiter (Redis-backed in prod) |
| Frontend | Tailwind CSS (CDN), Syne + Inter fonts |
| Deployment | Railway (Nixpacks) |

---

## Local setup (Docker — recommended)

```bash
# 1. Clone and enter the project
cp .env.example .env        # edit values as needed

# 2. Start Postgres + app together
docker-compose up --build
```

The compose file handles migrations automatically on startup.
Visit http://localhost:5000

---

## Local setup (native Postgres)

```bash
# 1. Create the database
createdb ziva

# 2. Install dependencies
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set DATABASE_URL=postgresql://user:pass@localhost:5432/ziva

# 4. Run migrations + seed data
python init_db.py

# 5. Start dev server
flask run
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask session secret — use a long random string in prod |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `MIGRATION_DATABASE_URL` | optional | Direct DB URL for Alembic (bypasses PgBouncer) |
| `RESEND_API_KEY` | ✅ | Resend API key for transactional email |
| `FROM_EMAIL` | ✅ | Sender address for outbound email |
| `APP_URL` | ✅ | Public URL of your deployment (used in email links) |
| `REDIS_URL` | optional | Redis URL — rate limiter falls back to memory if unset |
| `ADMIN_EMAIL` | optional | Used by `init_db.py` to create the first admin account |
| `ADMIN_PASSWORD` | optional | Used by `init_db.py` to create the first admin account |

---

## Deploy to Railway

1. Push repo to GitHub.
2. **New Project → Deploy from GitHub repo** in Railway dashboard.
3. Add a **PostgreSQL** plugin — Railway injects `DATABASE_URL` automatically.
4. (Optional) Add a **Redis** plugin — Railway injects `REDIS_URL` automatically.
5. Set remaining env vars (`SECRET_KEY`, `RESEND_API_KEY`, `FROM_EMAIL`, `APP_URL`) in Railway's Variables tab.
6. Railway runs `flask db upgrade && gunicorn app:app` on every deploy via `Procfile`.

> **First deploy only:** after the initial deploy succeeds, open a Railway shell and run `python init_db.py` to seed the default reward tiers.

---

## Migrations

```bash
# Create a new migration after changing models
flask db migrate -m "describe the change"
flask db upgrade

# Roll back one step
flask db downgrade
```

In production Railway auto-runs `flask db upgrade` before starting gunicorn (see `Procfile`).
If your Postgres is behind PgBouncer, set `MIGRATION_DATABASE_URL` to a direct connection string — `migrations/env.py` picks it up automatically.

---

## Admin dashboard

Visit `/admin` — no password required in the current build.
To re-enable auth, uncomment the `admin_required` decorator logic in `app.py`.

---

## Project structure

```
Ziva_Pro/
├── app.py                  # Flask app, models, routes
├── config.py               # Configuration (reads .env)
├── init_db.py              # One-time setup: migrate + seed + create admin
├── requirements.txt
├── Procfile                # Railway / Heroku start command
├── nixpacks.toml           # Railway Nixpacks build config
├── docker-compose.yml      # Local dev: Postgres + app
├── Dockerfile              # Multi-stage production image
├── .env.example
├── migrations/
│   ├── env.py              # Alembic environment (Flask-aware)
│   └── versions/
│       └── 0001_initial_schema.py   # Initial tables migration
└── templates/
    ├── base.html
    ├── index.html          # Landing / waitlist page
    ├── dashboard.html      # User referral dashboard
    └── admin.html          # Admin dashboard
```
