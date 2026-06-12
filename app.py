from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, abort, Response, make_response,
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_bcrypt import Bcrypt
from flask_talisman import Talisman
from flask_wtf.csrf import CSRFProtect
from flask_caching import Cache
from datetime import datetime, timedelta
from functools import wraps
import secrets, os, csv, io, hashlib, hmac, resend, threading
from urllib.parse import quote as url_quote

from config import Config

app = Flask(__name__)
app.config.from_object(Config)

# ── Startup guards — fail fast rather than silently misbehave ─────────────
_env = os.environ.get
if not app.debug:
    assert app.config["SECRET_KEY"] != "dev-secret-change-in-production", (
        "SECRET_KEY is the insecure default. Set a strong value in your environment."
    )
    assert app.config.get("ADMIN_PASSWORD_HASH"), (
        "ADMIN_PASSWORD_HASH is not set. Generate one with flask_bcrypt and add it to env."
    )
    assert os.environ.get("REDIS_URL"), (
        "REDIS_URL is not set. In-memory rate limiting resets on restart and is unsafe in prod."
    )

# ── Set permanent session lifetime once at startup, not per-request ───────
app.permanent_session_lifetime = timedelta(hours=8)

db      = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt  = Bcrypt(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=app.config["RATELIMIT_STORAGE_URI"],
    default_limits=["200 per day", "50 per hour"],
)
resend.api_key = app.config["RESEND_API_KEY"]

# ── Security headers (CSP, HSTS, X-Frame-Options, etc.) ────────────────────
# CSP is permissive on script/style because the UI relies on the Tailwind CDN,
# Google Fonts, and inline <script>/<style> blocks in base.html.
_csp = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'", "https://cdn.tailwindcss.com"],
    "style-src": ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
    "font-src": ["'self'", "https://fonts.gstatic.com", "https://fonts.googleapis.com"],
    "img-src": ["'self'", "data:"],
    "connect-src": "'self'",
}
Talisman(app, force_https=not app.debug, content_security_policy=_csp)

# ── CSRF protection on all state-mutating forms ─────────────────────────────
csrf = CSRFProtect(app)

# ── Simple cache for admin KPI queries (30s TTL) ────────────────────────────
cache = Cache(app, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 30})

# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = "users"
    id                = db.Column(db.Integer, primary_key=True)
    email             = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash     = db.Column(db.String(255))        # admin accounts only
    verified          = db.Column(db.Boolean, default=False, nullable=False)
    email_verify_token= db.Column(db.String(64), unique=True, nullable=True, index=True)
    token_expires_at  = db.Column(db.DateTime, nullable=True)
    referral_code     = db.Column(db.String(16), unique=True, nullable=False, index=True)
    referred_by       = db.Column(
        db.String(16), db.ForeignKey("users.referral_code"), nullable=True
    )
    referrals_count   = db.Column(db.Integer, default=0, nullable=False)
    waitlist_position = db.Column(db.Integer)
    _pending_ref_code = db.Column(db.String(16), nullable=True)   # cleared after verify
    joined_at         = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    verified_at       = db.Column(db.DateTime)

    referrals     = db.relationship(
        "User", foreign_keys=[referred_by],
        backref=db.backref("referrer", remote_side=[referral_code])
    )
    claimed_rewards = db.relationship("ClaimedReward", backref="user", lazy="dynamic")

    def set_password(self, password: str):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return bcrypt.check_password_hash(self.password_hash, password)

    def generate_verify_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.email_verify_token = token
        self.token_expires_at   = datetime.utcnow() + timedelta(hours=24)
        return token

    def get_next_reward(self, tiers):
        """Pass pre-fetched tiers — avoids a DB query per user."""
        for t in tiers:
            if t.referrals_required > self.referrals_count:
                return t
        return None

    def get_unlocked_rewards(self, tiers):
        """Pass pre-fetched tiers — avoids a DB query per user."""
        return [t for t in tiers if t.referrals_required <= self.referrals_count]


class RewardTier(db.Model):
    __tablename__ = "reward_tiers"
    id                 = db.Column(db.Integer, primary_key=True)
    referrals_required = db.Column(db.Integer, nullable=False, unique=True)
    reward_name        = db.Column(db.String(255), nullable=False)
    reward_description = db.Column(db.Text)
    badge_emoji        = db.Column(db.String(8), default="🎁")


class ClaimedReward(db.Model):
    __tablename__ = "claimed_rewards"
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    tier_id    = db.Column(db.Integer, db.ForeignKey("reward_tiers.id"), nullable=False)
    claimed_at = db.Column(db.DateTime, default=datetime.utcnow)
    tier       = db.relationship("RewardTier")


# ──────────────────────────────────────────────────────────────────────────────
# Admin auth
# ──────────────────────────────────────────────────────────────────────────────

ADMIN_SESSION_KEY = "admin_logged_in"


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(ADMIN_SESSION_KEY):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ──────────────────────────────────────────────────────────────────────────────
# Fraud / duplicate detection helpers
# ──────────────────────────────────────────────────────────────────────────────

def _email_hash(email: str) -> str:
    """SHA-256 fingerprint used for cross-variant duplicate detection."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


def _normalise_email(email: str) -> str:
    """Lowercase + strip; for Gmail strip dots & +tags from local part."""
    email = email.lower().strip()
    try:
        local, domain = email.split("@", 1)
        if domain in ("gmail.com", "googlemail.com"):
            local = local.split("+")[0].replace(".", "")
        email = f"{local}@{domain}"
    except ValueError:
        pass
    return email


def _is_self_referral(new_user_email: str, ref_code: str | None) -> bool:
    if not ref_code:
        return False
    referrer = User.query.filter_by(referral_code=ref_code).first()
    if referrer and _normalise_email(referrer.email) == _normalise_email(new_user_email):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Email helpers (Resend)
# ──────────────────────────────────────────────────────────────────────────────

def send_verification_email(user: User):
    """Send the email-verification link."""
    verify_url = f"{app.config['APP_URL']}/verify/{user.email_verify_token}"
    try:
        resend.Emails.send({
            "from":    app.config["FROM_EMAIL"],
            "to":      user.email,
            "subject": "Confirm your Ziva waitlist spot ✉️",
            "html": f"""
            <h2 style="font-family:sans-serif">Almost there!</h2>
            <p style="font-family:sans-serif">
              Click the button below to confirm your email and secure your place on the
              Ziva Pro waitlist. This link expires in <strong>24 hours</strong>.
            </p>
            <p>
              <a href="{verify_url}"
                 style="display:inline-block;padding:12px 28px;background:#7c3aed;
                        color:#fff;border-radius:8px;text-decoration:none;
                        font-family:sans-serif;font-weight:600">
                Verify my email
              </a>
            </p>
            <p style="font-family:sans-serif;color:#6b7280;font-size:13px">
              Or paste this link in your browser:<br>{verify_url}
            </p>
            """,
        })
    except Exception as e:
        app.logger.error(f"Verification email error for {user.email}: {e}")


def send_welcome_email(user: User):
    dashboard_url    = f"{app.config['APP_URL']}/dashboard/{user.referral_code}"
    referral_url     = f"{app.config['APP_URL']}/?ref={user.referral_code}"
    encoded_ref      = url_quote(referral_url, safe="")
    wa_url = (
        "https://wa.me/?text="
        + f"I%20just%20joined%20Ziva%20Pro%20early%20access%21%20Get%20your%20spot%20%F0%9F%9A%80%20{encoded_ref}"
    )
    x_url = (
        "https://twitter.com/intent/tweet?text="
        + f"I%20just%20joined%20%40ZivaPro%20early%20access%20%E2%80%94%20get%20yours%3A&url={encoded_ref}"
    )
    tg_url = (
        "https://t.me/share/url?url="
        + encoded_ref
        + "&text=Join%20me%20on%20Ziva%20Pro%20early%20access%21"
    )
    try:
        resend.Emails.send({
            "from":    app.config["FROM_EMAIL"],
            "to":      user.email,
            "subject": "You're on the Ziva waitlist 🎉",
            "html": f"""
            <h2 style="font-family:sans-serif">Welcome to Ziva Pro!</h2>
            <p style="font-family:sans-serif">
              Your waitlist spot is confirmed. Share your referral link to move up faster:
            </p>
            <p>
              <a href="{dashboard_url}"
                 style="display:inline-block;padding:12px 28px;background:#7c3aed;
                        color:#fff;border-radius:8px;text-decoration:none;
                        font-family:sans-serif;font-weight:600">
                View my dashboard
              </a>
            </p>
            <p style="font-family:sans-serif">Share on:</p>
            <p>
              <a href="{wa_url}" style="margin-right:12px">WhatsApp</a>
              <a href="{x_url}"  style="margin-right:12px">X / Twitter</a>
              <a href="{tg_url}">Telegram</a>
            </p>
            <p style="font-family:sans-serif;color:#6b7280;font-size:13px">
              Referral link: {referral_url}
            </p>
            """,
        })
    except Exception as e:
        app.logger.error(f"Resend welcome error for {user.email}: {e}")


def send_referral_milestone_email(user: User, reward: RewardTier):
    try:
        resend.Emails.send({
            "from":    app.config["FROM_EMAIL"],
            "to":      user.email,
            "subject": f"{reward.badge_emoji} You unlocked: {reward.reward_name}",
            "html": f"""
            <h2 style="font-family:sans-serif">Milestone reached!</h2>
            <p style="font-family:sans-serif">
              You've referred <strong>{user.referrals_count}</strong> friends and unlocked
              <strong>{reward.reward_name}</strong>.
            </p>
            <p style="font-family:sans-serif">{reward.reward_description or ''}</p>
            """,
        })
    except Exception as e:
        app.logger.error(f"Resend milestone error for {user.email}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Referral engine
# ──────────────────────────────────────────────────────────────────────────────

def process_referral(new_user: User, ref_code: str | None):
    """Credit referrer; enforce fraud rules; unlock reward tiers."""
    if not ref_code:
        return
    if ref_code == new_user.referral_code:
        app.logger.warning(f"Self-referral attempt blocked for {new_user.email}")
        return
    if _is_self_referral(new_user.email, ref_code):
        app.logger.warning(f"Email self-referral blocked for {new_user.email}")
        return

    referrer = User.query.filter_by(referral_code=ref_code).first()
    if not referrer:
        return

    new_user.referred_by    = ref_code
    referrer.referrals_count += 1

    # Single query for claimed IDs — avoids lazy dynamic relationship N+1
    already_claimed = {
        row[0] for row in
        db.session.query(ClaimedReward.tier_id)
        .filter(ClaimedReward.user_id == referrer.id)
        .all()
    }
    unlocked = RewardTier.query.filter(
        RewardTier.referrals_required <= referrer.referrals_count,
        RewardTier.id.notin_(already_claimed or [0]),
    ).all()
    for tier in unlocked:
        db.session.add(ClaimedReward(user_id=referrer.id, tier_id=tier.id))
        send_referral_milestone_email(referrer, tier)


# ──────────────────────────────────────────────────────────────────────────────
# Public routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    total = User.query.count()
    return render_template("index.html", total=total)


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/join", methods=["POST"])
@limiter.limit("5 per minute")
def join():
    raw_email = request.form.get("email", "").strip()
    if not raw_email or "@" not in raw_email:
        flash("Please enter a valid email address.", "error")
        return redirect("/")

    email = _normalise_email(raw_email)

    # ── Duplicate protection (normalised email check) ──────────────────────
    if User.query.filter_by(email=email).first():
        flash("That email is already on the list!", "info")
        return redirect("/")

    ref_code = request.args.get("ref") or request.form.get("ref")

    user = User(
        email=email,
        verified=False,                   # requires email verification now
        referral_code=secrets.token_hex(8),
        waitlist_position=User.query.count() + 1,
        _pending_ref_code=ref_code,       # stashed; credited only on verify
    )
    token = user.generate_verify_token()
    db.session.add(user)
    db.session.commit()

    send_verification_email(user)
    return render_template("verify_pending.html", email=email)


@app.route("/verify/<token>")
def verify_email(token: str):
    user = User.query.filter_by(email_verify_token=token).first_or_404()

    if user.verified:
        return redirect(url_for("dashboard", code=user.referral_code))

    if user.token_expires_at and datetime.utcnow() > user.token_expires_at:
        flash("That verification link has expired. Request a new one below.", "error")
        return render_template("verify_expired.html", email=user.email)

    user.verified             = True
    user.verified_at          = datetime.utcnow()
    user.email_verify_token   = None
    user.token_expires_at     = None

    # Credit referral only on confirmed email — fraud prevention
    pending_ref = user._pending_ref_code
    user._pending_ref_code = None
    process_referral(user, pending_ref)

    db.session.commit()

    threading.Thread(target=send_welcome_email, args=(user,), daemon=True).start()
    flash("Email verified! Welcome to Ziva Pro 🎉", "success")
    return redirect(url_for("dashboard", code=user.referral_code))


@app.route("/resend-verification", methods=["POST"])
@limiter.limit("3 per hour")
def resend_verification():
    email = _normalise_email(request.form.get("email", "").strip())
    user  = User.query.filter_by(email=email).first()
    if user and not user.verified:
        user.generate_verify_token()
        db.session.commit()
        send_verification_email(user)
    flash("If that email is on our list, a new verification link is on its way.", "info")
    return redirect("/")


@app.route("/dashboard/<code>")
def dashboard(code: str):
    user = User.query.filter_by(referral_code=code).first_or_404()
    leaderboard = (
        User.query
        .filter(User.verified == True)
        .order_by(User.referrals_count.desc())
        .limit(10)
        .all()
    )
    # Fetch tiers once — pass to helpers to avoid N+1 per user property
    tiers            = RewardTier.query.order_by(RewardTier.referrals_required).all()
    next_reward      = user.get_next_reward(tiers)
    unlocked_rewards = user.get_unlocked_rewards(tiers)

    referral_url = f"{app.config['APP_URL']}/?ref={user.referral_code}"
    encoded_ref  = url_quote(referral_url, safe="")

    # Share URLs — referral_url is percent-encoded before embedding as a query param
    wa_url = (
        "https://wa.me/?text="
        + f"I%20just%20joined%20Ziva%20Pro%20early%20access%21%20Join%20here%3A%20{encoded_ref}"
    )
    x_url = (
        "https://twitter.com/intent/tweet?text="
        + f"I%20just%20joined%20%40ZivaPro%20early%20access%20%E2%80%94%20get%20yours%3A&url={encoded_ref}"
    )
    tg_url = (
        "https://t.me/share/url?url="
        + encoded_ref
        + "&text=Join%20me%20on%20Ziva%20Pro%20early%20access%21"
    )

    return render_template(
        "dashboard.html",
        user=user,
        leaderboard=leaderboard,
        tiers=tiers,
        next_reward=next_reward,
        unlocked_rewards=unlocked_rewards,
        referral_url=referral_url,
        wa_url=wa_url,
        x_url=x_url,
        tg_url=tg_url,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Admin auth routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def admin_login():
    if session.get(ADMIN_SESSION_KEY):
        return redirect(url_for("admin"))

    # Guard: if hash not configured, refuse login rather than 500
    if not app.config.get("ADMIN_PASSWORD_HASH"):
        flash("Admin account not configured. Set ADMIN_PASSWORD_HASH in environment.", "error")
        return render_template("admin_login.html")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Constant-time username check; bcrypt verify for password
        username_ok = hmac.compare_digest(username, app.config["ADMIN_USERNAME"])
        password_ok = (
            username_ok
            and bcrypt.check_password_hash(app.config["ADMIN_PASSWORD_HASH"], password)
        )

        if username_ok and password_ok:
            session[ADMIN_SESSION_KEY] = True
            session.permanent = True
            return redirect(url_for("admin"))

        flash("Invalid credentials.", "error")

    return render_template("admin_login.html")


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop(ADMIN_SESSION_KEY, None)
    return redirect(url_for("admin_login"))


# ──────────────────────────────────────────────────────────────────────────────
# Admin dashboard
# ──────────────────────────────────────────────────────────────────────────────

@cache.cached(timeout=30, key_prefix="admin_kpis")
def get_admin_kpis():
    """One aggregation query + one leaderboard query, cached 30s."""
    week_ago = datetime.utcnow() - timedelta(days=7)

    row = db.session.query(
        db.func.count(User.id),
        db.func.sum(db.case((User.verified == True, 1), else_=0)),
        db.func.sum(User.referrals_count),
        db.func.sum(db.case((User.referred_by.isnot(None), 1), else_=0)),
        db.func.sum(db.case((User.joined_at >= week_ago, 1), else_=0)),
    ).one()

    total_users     = row[0] or 0
    verified_users  = row[1] or 0
    total_referrals = row[2] or 0
    referred_count  = row[3] or 0
    recent_signups  = row[4] or 0
    conversion_rate = round(referred_count / total_users * 100, 1) if total_users else 0

    leaderboard = (
        User.query
        .filter(User.verified == True)
        .order_by(User.referrals_count.desc())
        .limit(10)
        .all()
    )

    return {
        "total_users": total_users,
        "verified_users": verified_users,
        "total_referrals": total_referrals,
        "referred_count": referred_count,
        "conversion_rate": conversion_rate,
        "recent_signups": recent_signups,
        "leaderboard": leaderboard,
    }


@app.route("/admin")
@admin_required
def admin():
    # ── Search ────────────────────────────────────────────────────────────────
    q    = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    query = User.query.order_by(User.referrals_count.desc(), User.joined_at)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(User.email.ilike(like), User.referral_code.ilike(like))
        )

    # ── Pagination ────────────────────────────────────────────────────────────
    per_page   = 25
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    users      = pagination.items

    tiers = RewardTier.query.order_by(RewardTier.referrals_required).all()

    # ── Analytics (cached 30s — KPIs don't need to be real-time) ───────────────
    kpis = get_admin_kpis()
    total_users     = kpis["total_users"]
    verified_users  = kpis["verified_users"]
    total_referrals = kpis["total_referrals"]
    referred_count  = kpis["referred_count"]
    conversion_rate = kpis["conversion_rate"]
    recent_signups  = kpis["recent_signups"]
    leaderboard     = kpis["leaderboard"]

    return render_template(
        "admin.html",
        users=users,
        tiers=tiers,
        pagination=pagination,
        q=q,
        total_users=total_users,
        verified_users=verified_users,
        total_referrals=total_referrals,
        referred_count=referred_count,
        conversion_rate=conversion_rate,
        recent_signups=recent_signups,
        leaderboard=leaderboard,
    )


@app.route("/admin/reward", methods=["POST"])
@admin_required
def admin_add_reward():
    tier = RewardTier(
        referrals_required=int(request.form["referrals_required"]),
        reward_name=request.form["reward_name"],
        reward_description=request.form.get("reward_description", ""),
        badge_emoji=request.form.get("badge_emoji", "🎁"),
    )
    db.session.add(tier)
    db.session.commit()
    cache.delete("admin_kpis")
    flash(f"Reward tier '{tier.reward_name}' added.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id: int):
    user = User.query.get_or_404(user_id)
    # Nullify referred_by on users who signed up via this code, but do NOT
    # touch their own referrals_count — that reflects their own activity.
    User.query.filter_by(referred_by=user.referral_code).update(
        {"referred_by": None}
    )
    ClaimedReward.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    cache.delete("admin_kpis")
    flash(f"User {user.email} deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/export/csv")
@admin_required
def admin_export_csv():
    """Download all verified users as CSV."""
    users = (
        User.query
        .filter_by(verified=True)
        .order_by(User.waitlist_position)
        .all()
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "position", "email", "referral_code", "referred_by",
        "referrals_count", "verified_at", "joined_at",
    ])
    for u in users:
        writer.writerow([
            u.waitlist_position,
            u.email,
            u.referral_code,
            u.referred_by or "",
            u.referrals_count,
            u.verified_at.isoformat() if u.verified_at else "",
            u.joined_at.isoformat(),
        ])
    output = out.getvalue()
    resp   = make_response(output)
    resp.headers["Content-Disposition"] = (
        f"attachment; filename=ziva_waitlist_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    )
    resp.headers["Content-Type"] = "text/csv"
    return resp


# ──────────────────────────────────────────────────────────────────────────────
# Health check (required by Railway / Render zero-downtime deploys)
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Lightweight liveness probe — no DB hit intentional."""
    return "", 200


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False)
