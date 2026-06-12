"""
app.py — Flask backend cho hệ thống tạo tạp chí PDF
Fix: thêm role admin, redirect đúng trang sau login
"""

import os
import threading
import traceback
import uuid
import hmac
import hashlib
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, url_for,redirect, session, flash, abort, jsonify, send_from_directory)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from processor import process_docx_to_pdf, TEMPLATE_NAMES
from seepay_payment import setup_seepay_routes
from dotenv import load_dotenv

load_dotenv()

_REQUIRED_ENV = ["SECRET_KEY"]
_missing = [k for k in _REQUIRED_ENV if not os.getenv(k)]
if _missing:
    raise RuntimeError(f"[SECURITY] Thiếu biến môi trường: {', '.join(_missing)}")

app = Flask(__name__)

# THÊM DÒNG NÀY
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.template_filter('vntime')
def vntime_filter(dt):
    if not dt:
        return ""
    return (dt + timedelta(hours=7)).strftime('%d/%m/%Y %H:%M')

# Check if running on Vercel (read-only filesystem, use /tmp for writes)
IS_VERCEL = "VERCEL" in os.environ
upload_folder = "/tmp" if IS_VERCEL else "static/outputs"

# Construct SQLALCHEMY_DATABASE_URI dynamically if separate env vars are provided
db_uri = os.getenv("DATABASE_URL") or os.getenv("SQLALCHEMY_DATABASE_URI")
if db_uri:
    if db_uri.startswith("mysql://"):
        db_uri = db_uri.replace("mysql://", "mysql+pymysql://", 1)
else:
    db_user = os.getenv("DB_USER")
    db_pwd = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "3306")
    db_name = os.getenv("DB_NAME")
    if db_user and db_host and db_name:
        db_uri = f"mysql+pymysql://{db_user}:{db_pwd or ''}@{db_host}:{db_port}/{db_name}"
    else:
        db_uri = "mysql+pymysql://root:@localhost:3306/ai_tapchi"

app.config.update(
    SECRET_KEY                     = os.environ["SECRET_KEY"],
    UPLOAD_FOLDER                  = upload_folder,
    MAX_CONTENT_LENGTH             = 500 * 1024 * 1024, # Tăng lên 500MB để tránh lỗi 413
    SQLALCHEMY_DATABASE_URI        = db_uri,
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    SESSION_COOKIE_HTTPONLY        = True,
    SESSION_COOKIE_SAMESITE        = "Lax",
    SESSION_COOKIE_SECURE          = True,
    REMEMBER_COOKIE_HTTPONLY       = True,
    REMEMBER_COOKIE_DURATION       = 0,
)

# Custom route to serve output files from UPLOAD_FOLDER (critical for Vercel /tmp directory writes)
@app.route('/static/outputs/<path:filename>')
def serve_outputs(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

_OPENROUTER_KEY       = os.getenv("OPENROUTER_API_KEY", "")
_SEPAY_WEBHOOK_SECRET = os.getenv("SEPAY_WEBHOOK_SECRET", "")
_SEPAY_BANK_NAME      = os.getenv("SEPAY_BANK_NAME", "MB Bank")
_SEPAY_ACCOUNT_NUMBER = os.getenv("SEPAY_ACCOUNT_NUMBER", "")
_SEPAY_ACCOUNT_NAME   = os.getenv("SEPAY_ACCOUNT_NAME", "AI Tap Chi")

_GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
_GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
_GOOGLE_ENABLED       = bool(_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET)

if not _GOOGLE_ENABLED:
    print("[WARN] Google OAuth chua cau hinh")
else:
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


db            = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view             = "login"
login_manager.login_message          = "Vui lòng đăng nhập để tiếp tục."
login_manager.login_message_category = "warning"

oauth = OAuth(app)
if _GOOGLE_ENABLED:
    google = oauth.register(
        name                = "google",
        client_id           = _GOOGLE_CLIENT_ID,
        client_secret       = _GOOGLE_CLIENT_SECRET,
        server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs       = {"scope": "openid email profile"},
    )
else:
    google = None

_ALLOWED_LOGO = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
def _allowed_logo(filename):
    return ("." in filename and filename.rsplit(".", 1)[1].lower() in _ALLOWED_LOGO)

# Global memory to store job statuses
jobs = {}


class User(UserMixin, db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(150), unique=True, nullable=False)
    name       = db.Column(db.String(150))
    password   = db.Column(db.String(200))
    google_id  = db.Column(db.String(200))
    auth_type  = db.Column(db.String(20), default="local")
    role       = db.Column(db.String(20), default="user")
    is_active  = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    picture    = db.Column(db.String(255))
    balance    = db.Column(db.Integer, default=0)


class Payment(db.Model):
    id                   = db.Column(db.Integer, primary_key=True)
    user_id              = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    payment_code         = db.Column(db.String(32), unique=True, nullable=False, index=True)
    amount               = db.Column(db.Integer, nullable=False)
    currency             = db.Column(db.String(10), nullable=False, default="VND")
    status               = db.Column(db.String(20), nullable=False, default="pending")
    transfer_content     = db.Column(db.String(120), nullable=False)
    sepay_transaction_id = db.Column(db.String(80))
    created_at           = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    paid_at              = db.Column(db.DateTime)


class MagazineHistory(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title        = db.Column(db.String(255))
    template     = db.Column(db.String(50))
    pdf_filename = db.Column(db.String(255))
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)


class SystemSetting(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text)


def get_setting(key, default=None):
    try:
        setting = SystemSetting.query.filter_by(key=key).first()
        return setting.value if setting else default
    except Exception:
        return default


def set_setting(key, value):
    try:
        setting = SystemSetting.query.filter_by(key=key).first()
        if not setting:
            setting = SystemSetting(key=key, value=str(value))
            db.session.add(setting)
        else:
            setting.value = str(value)
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


@app.template_filter("vntime")
def vntime_filter(dt):
    if not dt: return ""
    from datetime import timedelta
    # Giả định server UTC, VN là UTC+7
    vn_dt = dt + timedelta(hours=7)
    return vn_dt.strftime("%d/%m/%Y %H:%M")

@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user and user.is_active == 0:
        return None
    return user


with app.app_context():
    db.create_all()
    from sqlalchemy import text
    for col, definition in [
        ("role",       "VARCHAR(20) DEFAULT 'user'"),
        ("is_active",  "TINYINT(1) DEFAULT 1"),
        ("created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("last_login", "DATETIME"),
        ("picture",    "VARCHAR(255)"),
        ("balance",    "INT DEFAULT 0"),
    ]:
        try:
            db.session.execute(text(f"ALTER TABLE user ADD COLUMN {col} {definition}"))
            db.session.commit()
            print(f"[MIGRATE] Added column: {col}")
        except Exception:
            db.session.rollback()

    # Thêm cấu hình mặc định nếu chưa có
    try:
        defaults = {
            "llm_provider": "openrouter",
            "llm_model": "openai/gpt-4o-mini",
            "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
            "openai_api_key": "",
            "pricing_per_magazine": "10000",
            "pricing_packages": '[{"name": "Gói Đồng", "amount": 20000, "desc": "Tạo được 2 tạp chí cao cấp"}, {"name": "Gói Bạc", "amount": 50000, "desc": "Tạo được 5 tạp chí + Tặng thêm 5%"}, {"name": "Gói Vàng", "amount": 100000, "desc": "Tạo được 10 tạp chí + Tặng thêm 15%"}, {"name": "Gói Kim Cương", "amount": 200000, "desc": "Tạo được 20 tạp chí + Tặng thêm 30%"}]'
        }
        for k, v in defaults.items():
            s = SystemSetting.query.filter_by(key=k).first()
            if not s:
                db.session.add(SystemSetting(key=k, value=v))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[INIT SETTINGS ERROR]: {e}")

    # Tự động tạo tài khoản admin mặc định nếu chưa có tài khoản nào
    try:
        if not User.query.first():
            admin_user = User(
                email="admin@gmail.com",
                name="System Admin",
                password=generate_password_hash("123456"),
                role="admin",
                is_active=1,
                balance=100000000
            )
            db.session.add(admin_user)
            db.session.commit()
            print("[INIT] Created default admin account: admin@gmail.com / 123456")
    except Exception as e:
        db.session.rollback()
        print(f"[INIT ADMIN ERROR]: {e}")

from admin import register_admin
register_admin(app, db, User, Payment, MagazineHistory, SystemSetting, get_setting, set_setting)


def _redirect_by_role(user):
    db.session.refresh(user)
    role = user.role or "user"
    print(f"[LOGIN] email={user.email!r} role={role!r}")
    if role == "admin":
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("editor"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not name or not email or not password:
            flash("Please fill in all information!", "error")
            return render_template("register.html", active_page='register')
        if len(password) < 6:
            flash("Password must be at least 6 characters!", "error")
            return render_template("register.html", active_page='register')
        if User.query.filter_by(email=email).first():
            flash("This email is already in use!", "error")
            return render_template("register.html", active_page='register')
        user = User(
            email=email, name=name,
            password=generate_password_hash(password),
            auth_type="local", role="user"
        )
        db.session.add(user)
        db.session.commit()
        flash("Registration successful! Please login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html", active_page='register')


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        user     = User.query.filter_by(email=email, auth_type="local").first()
        if not user or not check_password_hash(user.password, password):
            flash("Incorrect email or password!", "error")
            return render_template("login.html", google_enabled=_GOOGLE_ENABLED, active_page='login')
        if user.is_active == 0:
            flash("Your account is locked. Please contact Admin.", "error")
            return render_template("login.html", google_enabled=_GOOGLE_ENABLED, active_page='login')
        user.last_login = datetime.utcnow()
        db.session.commit()
        login_user(user, remember=False)
        db.session.refresh(user)
        role = user.role or "user"
        print(f"[LOGIN] email={user.email!r} role={role!r}")
        if role == "admin":
            flash(f"Chào mừng Admin {user.name}!", "success")
            return redirect(url_for("admin.dashboard"))
        else:
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("editor"))
    return render_template("login.html", google_enabled=_GOOGLE_ENABLED, active_page='login')


@app.route("/login/google")
def login_google():
    if not _GOOGLE_ENABLED:
        flash("Google OAuth chưa được cấu hình.", "warning")
        return redirect(url_for("login"))
    session.clear()
    # Tự động dùng https khi qua ngrok, http khi localhost
    if (request.host.endswith(".ngrok-free.dev") or 
        request.host.endswith(".ngrok.io") or 
        request.host.endswith(".trycloudflare.com") or
        request.host.endswith(".vercel.app")):
        scheme = "https"
    else:
        scheme = "http"
    redirect_uri = url_for("google_callback", _external=True, _scheme=scheme)
    print(f"[DEBUG] Redirect URI: {redirect_uri}")
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    if not _GOOGLE_ENABLED or not google:
        abort(404)
    try:
        token     = google.authorize_access_token()
        user_info = token.get("userinfo")
    except Exception as e:
        flash(f"Lỗi kết nối Google: {str(e)}", "error")
        return redirect(url_for("login"))
    if not user_info:
        flash("Không lấy được thông tin Google!", "error")
        return redirect(url_for("login"))
    email = user_info.get("email", "").lower()
    name  = user_info.get("name", email)
    gid   = user_info.get("sub")
    pic   = user_info.get("picture")
    
    user  = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name, google_id=gid, auth_type="google", role="user", picture=pic)
        db.session.add(user)
        db.session.commit()
        flash(f"Welcome, {name}!", "success")
    elif user.auth_type == "local":
        flash("This email is registered with a password.", "warning")
        return redirect(url_for("login"))
    else:
        if user.is_active == 0:
            flash("Your account is locked. Please contact Admin.", "error")
            return redirect(url_for("login"))
        user.picture = pic # Update picture if changed
        flash(f"Welcome back, {user.name}!", "success")
    user.last_login = datetime.utcnow()
    db.session.commit()
    login_user(user, remember=False)
    return _redirect_by_role(user)


@app.route("/logout")
def logout():
    logout_user()
    session.clear()
    response = redirect(url_for("landing"))
    response.delete_cookie("remember_token")
    flash("Logged out.", "info")
    return response


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


def _verify_sepay_signature(raw_body, signature):
    # Neu chua cau hinh secret → chap nhan tat ca (dev mode)
    if not _SEPAY_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    digest = hmac.new(
        _SEPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, signature.lower())


@app.route("/billing", methods=["GET", "POST"])
@login_required
def billing():
    if request.method == "POST":
        raw_amount = request.form.get("amount", "0").strip()
        try:
            amount = int(raw_amount)
        except ValueError:
            amount = 0
        if amount < 10000:
            flash("Số tiền tối thiểu là 10,000 VND.", "error")
            return redirect(url_for("billing"))
        code = f"MAG{uuid.uuid4().hex[:8].upper()}"
        payment = Payment(user_id=current_user.id, payment_code=code, amount=amount, transfer_content=code)
        db.session.add(payment)
        db.session.commit()
        flash("Đã tạo yêu cầu thanh toán.", "success")
        return redirect(url_for("billing", code=code))
    code = request.args.get("code", "").strip().upper()
    current_payment = None
    if code:
        current_payment = Payment.query.filter_by(user_id=current_user.id, payment_code=code).first()
    if not current_payment:
        current_payment = Payment.query.filter_by(user_id=current_user.id).order_by(Payment.id.desc()).first()
    
    time_left = 300
    if current_payment and current_payment.status == "pending":
        elapsed = (datetime.utcnow() - current_payment.created_at).total_seconds()
        time_left = int(300 - elapsed)
        if time_left <= 0:
            current_payment.status = "cancelled"
            db.session.commit()
            time_left = 0

    recent_payments = Payment.query.filter_by(user_id=current_user.id).order_by(Payment.id.desc()).limit(10).all()
    
    # Lấy và phân tích các gói nạp từ cấu hình hệ thống
    packages_json = get_setting("pricing_packages", "[]")
    import json as py_json
    try:
        pricing_packages = py_json.loads(packages_json)
    except Exception:
        pricing_packages = []

    return render_template("billing_sepay.html", payment=current_payment, recent_payments=recent_payments,
        sepay_bank_name=_SEPAY_BANK_NAME, sepay_account_number=_SEPAY_ACCOUNT_NUMBER, sepay_account_name=_SEPAY_ACCOUNT_NAME, 
        time_left=time_left, active_page='billing', pricing_packages=pricing_packages)


@app.route("/api/payment-status/<payment_code>")
@login_required
def payment_status(payment_code):
    payment = Payment.query.filter_by(user_id=current_user.id, payment_code=payment_code.upper()).first()
    if not payment:
        return jsonify({"ok": False, "message": "Không tìm thấy"}), 404
    return jsonify({
        "ok": True,
        "payment_code": payment.payment_code,
        "status": payment.status,
        "amount": payment.amount,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "is_paid": payment.status == "paid"
    })

@app.route("/api/cancel-payment/<payment_code>", methods=["POST"])
@login_required
def cancel_payment(payment_code):
    payment = Payment.query.filter_by(user_id=current_user.id, payment_code=payment_code.upper(), status="pending").first()
    if not payment:
        return jsonify({"ok": False, "message": "Không tìm thấy giao dịch đang chờ"}), 404
    payment.status = "cancelled"
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/api/extract-docx-metadata", methods=["POST"])
@login_required
def extract_docx_metadata():
    file = request.files.get("file")
    if not file or not file.filename.endswith(".docx"):
        return jsonify({"success": False, "message": "File không hợp lệ"}), 400
    
    try:
        from docx import Document
        import re
        doc = Document(file)
        
        # 1. Thử lấy từ core_properties
        author = ""
        try:
            author = doc.core_properties.author or ""
            author = author.strip()
        except:
            pass
            
        # 2. Nếu core_properties không có hoặc là tên mặc định chung chung
        invalid_authors = {
            "admin", "administrator", "user", "windows user", "microsoft", "author", 
            "unknown", "pc", "laptop", "un-named", "unnamed", "un-name", "unnamed user", 
            "editor", "tác giả", "viết bởi", "by", "n/a", "none", "null", "undefined"
        }
        if author.lower().strip() in invalid_authors:
            author = ""
            
        if not author:
            # Quét 15 paragraph đầu tiên để tìm tên tác giả
            count = 0
            for p in doc.paragraphs:
                text = p.text.strip()
                if not text:
                    continue
                count += 1
                if count > 15:
                    break
                # Regex tìm các mẫu: Tác giả: ..., Author: ..., Viết bởi: ..., By: ...
                match = re.search(r"^(tác\s+giả|author|viết\s+bởi|by|bài\s+viết|photo\s+by)\s*[:\-–—]?\s*(.+)$", text, re.IGNORECASE)
                if match:
                    possible_author = match.group(2).strip()
                    possible_author = re.sub(r"^[\"'“‘]+|[\"'”’]+$", "", possible_author).strip()
                    if possible_author and possible_author.lower().strip() not in invalid_authors and len(possible_author) < 50:
                        author = possible_author
                        break
        
        return jsonify({
            "success": True,
            "author": author
        })
    except Exception as e:
        print(f"[METADATA EXTRACT ERROR]: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/webhook/sepay", methods=["POST", "GET"])
def sepay_webhook():
    # GET: test nhanh xem route co song khong
    if request.method == "GET":
        return jsonify({"ok": True, "alive": True, "route": "/webhook/sepay"}), 200
    raw_body  = request.get_data()
    signature = request.headers.get("X-Sepay-Signature", "")
    
    # Log để debug
    print(f"[WEBHOOK] Received. Body: {raw_body[:500]}")
    
    if not _verify_sepay_signature(raw_body, signature):
        print(f"[WEBHOOK] Invalid signature")
        return jsonify({"ok": False, "message": "Invalid signature"}), 401
    
    payload = request.get_json(silent=True) or {}
    print(f"[WEBHOOK] Payload: {payload}")
    
    # SePay có thể dùng nhiều field khác nhau
    transfer_content = str(
        payload.get("transferContent") or
        payload.get("content") or
        payload.get("description") or
        payload.get("transaction_content") or
        ""
    ).upper().strip()
    
    try:
        amount = int(float(
            payload.get("transferAmount") or
            payload.get("amount") or
            payload.get("transaction_amount") or
            0
        ))
    except (ValueError, TypeError):
        amount = 0

    transaction_id = str(
        payload.get("id") or
        payload.get("transaction_id") or
        payload.get("referenceCode") or
        ""
    )
    
    print(f"[WEBHOOK] transfer_content={transfer_content!r}, amount={amount}, tid={transaction_id!r}")
    
    if not transfer_content and amount <= 0:
        return jsonify({"ok": False, "message": "Invalid payload"}), 400
    
    pending = Payment.query.filter(Payment.status == "pending").order_by(Payment.id.desc()).all()
    matched = None
    for p in pending:
        code_upper = p.payment_code.upper()
        # Match nếu nội dung chuyển khoản chứa mã, hoặc mã chứa trong nội dung
        if code_upper in transfer_content or transfer_content in code_upper:
            if amount <= 0 or amount >= p.amount:
                matched = p
                print(f"[WEBHOOK] Matched payment: {p.payment_code}")
                break
    
    if not matched:
        print(f"[WEBHOOK] No match found among {len(pending)} pending payments")
        # Trả 200 để SePay không retry liên tục, nhưng log lại
        return jsonify({"ok": False, "message": "Payment not matched"}), 200
    
    matched.status = "paid"
    matched.sepay_transaction_id = transaction_id or matched.sepay_transaction_id
    matched.paid_at = datetime.utcnow()
    
    # Cộng tiền vào tài khoản user
    user = db.session.get(User, matched.user_id)
    if user:
        user.balance = (user.balance or 0) + matched.amount
        print(f"[WEBHOOK] Updated balance for user {user.email}: +{matched.amount}")

    db.session.commit()
    print(f"[WEBHOOK] Payment {matched.payment_code} marked as PAID")
    return jsonify({"ok": True, "payment_code": matched.payment_code})


@app.route("/view/<filename>")
@login_required
def view_flipbook(filename):
    # Ensure it only accesses the outputs directory
    safe_filename = os.path.basename(filename)
    pdf_url = url_for("static", filename=f"outputs/{safe_filename}")
    return render_template("flipbook.html", pdf_url=pdf_url)


@app.route("/")
def landing():
    pricing_per_magazine = get_setting("pricing_per_magazine", "10000")
    pricing_packages_json = get_setting("pricing_packages", "[]")
    import json as py_json
    try:
        pricing_packages = py_json.loads(pricing_packages_json)
    except Exception:
        pricing_packages = []
        
    return render_template("landing.html", active_page='landing', 
                           pricing_per_magazine=pricing_per_magazine, 
                           pricing_packages=pricing_packages)


def background_magazine_job(job_id, user_id, docx_paths, pdf_path, journal_meta, template_key):
    with app.app_context():
        try:
            print(f"[JOB {job_id}] Bắt đầu xử lý cho User {user_id}")
            
            # Lấy cấu hình LLM từ DB
            llm_provider = get_setting("llm_provider", "openrouter")
            llm_model = get_setting("llm_model", "openai/gpt-4o-mini")
            
            api_key = ""
            if llm_provider == "openai":
                api_key = get_setting("openai_api_key", "")
            elif llm_provider == "openrouter":
                api_key = get_setting("openrouter_api_key", "")
            elif llm_provider == "deepseek":
                api_key = get_setting("deepseek_api_key", "")
            elif llm_provider == "gemini":
                api_key = get_setting("gemini_api_key", "")
                
            # Fallback nếu API key trống
            if not api_key:
                api_key = os.environ.get("OPENROUTER_API_KEY", "")

            process_docx_to_pdf(
                file_paths=docx_paths, 
                output_folder=app.config["UPLOAD_FOLDER"], 
                output_pdf_path=pdf_path,
                journal_meta=journal_meta,
                api_key=api_key, 
                template_key=template_key,
                llm_provider=llm_provider,
                llm_model=llm_model
            )
            
            pdf_filename = os.path.basename(pdf_path)
            # Save history
            new_history = MagazineHistory(
                user_id=user_id,
                title=journal_meta.get("journal", "VOGUE"),
                template=TEMPLATE_NAMES.get(template_key, template_key),
                pdf_filename=pdf_filename
            )
            db.session.add(new_history)
            db.session.commit()
            
            pdf_url = f"/static/outputs/{pdf_filename}"
            jobs[job_id] = {"status": "done", "pdf_url": pdf_url}
            print(f"[JOB {job_id}] Hoàn tất!")
            
        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"[JOB {job_id}] LỖI NGHIÊM TRỌNG:\n{error_trace}")
            jobs[job_id] = {"status": "error", "message": str(e)}

@app.route("/api/job-status/<job_id>")
@login_required
def job_status(job_id):
    return jsonify(jobs.get(job_id, {"status": "pending"}))


@app.route("/editor", methods=["GET", "POST"])
@login_required
def editor():
    # Allow admins to access the editor too
    
    if request.method == "POST":
        print("====== [DEBUG POST] NHẬN YÊU CẦU TẠO TẠP CHÍ ======")
        try:
            # Kiểm tra tài khoản và trừ tiền
            pricing = int(get_setting("pricing_per_magazine", 10000))
            print(f"[DEBUG POST] Tài khoản: {current_user.email}, Giá: {pricing}, Số dư: {current_user.balance}")
            if current_user.balance < pricing:
                print(f"[DEBUG POST] LỖI: Số dư không đủ!")
                pricing_str = f"{pricing:,.0f}".replace(",", ".")
                balance_str = f"{current_user.balance:,.0f}".replace(",", ".")
                return jsonify({
                    "status": "error", 
                    "message": f"Số dư tài khoản không đủ! Mỗi lượt tạo tạp chí cần {pricing_str} VND. Số dư hiện tại của bạn là {balance_str} VND. Vui lòng nạp thêm tiền."
                }), 400

            print("[DEBUG POST] Đang đọc files và form data...")
            files = request.files.getlist("files")
            chapter_titles = request.form.getlist("chapter_titles")
            chapter_descs = request.form.getlist("chapter_descs")
            
            if not files or len(files) == 0:
                return jsonify({"status": "error", "message": "Vui lòng tải lên ít nhất 1 file .docx!"}), 400
            
            if len(files) > 10:
                return jsonify({"status": "error", "message": "Chỉ cho phép tải lên tối đa 10 file Word!"}), 400
            
            os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
            uid = str(uuid.uuid4())[:8]
            
            docx_paths = []
            for i, file in enumerate(files):
                if file and file.filename.endswith(".docx"):
                    safe_name = f"{uid}_ch{i}_{file.filename}"
                    dpath = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)
                    file.save(dpath)
                    docx_paths.append(dpath)
            
            if not docx_paths:
                return jsonify({"status": "error", "message": "Không tìm thấy file .docx hợp lệ!"}), 400
            
            cover_path = None
            cover_b64 = request.form.get("cover_base64")
            if cover_b64 and cover_b64.startswith("data:image"):
                import base64
                header, encoded = cover_b64.split(",", 1)
                cover_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uid}_cover.jpg")
                with open(cover_path, "wb") as f:
                    f.write(base64.b64decode(encoded))
            else:
                cover_file = request.files.get("cover_image")
                if cover_file and cover_file.filename and _allowed_logo(cover_file.filename):
                    ext        = cover_file.filename.rsplit(".", 1)[1].lower()
                    cover_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uid}_cover.{ext}")
                    cover_file.save(cover_path)
            
            back_cover_path = None
            back_cover_b64 = request.form.get("back_cover_base64")
            if back_cover_b64 and back_cover_b64.startswith("data:image"):
                import base64
                header, encoded = back_cover_b64.split(",", 1)
                back_cover_path = os.path.join(app.config["UPLOAD_FOLDER"], f"{uid}_back_cover.jpg")
                with open(back_cover_path, "wb") as f:
                    f.write(base64.b64decode(encoded))

            pdf_filename = f"{uid}_output.pdf"
            pdf_path     = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
            template_key = request.form.get("template_key", "VOGUE")
            
            author_name = request.form.get("author_name", "Nguyen Thanh Son").strip()
            if not author_name: author_name = "Nguyen Thanh Son"

            journal_meta = {
                "journal": request.form.get("magazine_title", "VOGUE"),
                "volume_issue": request.form.get("magazine_subtitle", "EDITORIAL ISSUE"),
                "pub_date": request.form.get("pub_date", "").strip(),
                "logo_left": cover_path,
                "back_cover_path": back_cover_path,
                "editor": author_name,
                "use_custom_cover": bool(cover_b64),
                "council_info": request.form.get("council_info", ""),
                "editorial_board": request.form.get("editorial_board", ""),
                "office_info": request.form.get("office_info", ""),
                "reps_info": request.form.get("reps_info", ""),
                "license_info": request.form.get("license_info", ""),
                "price": request.form.get("price", "30.000VND"),
                "chapter_titles": chapter_titles,
                "chapter_descs": chapter_descs
            }

            # Trừ tiền tài khoản người dùng
            current_user.balance -= pricing
            db.session.commit()

            job_id = f"job_{uid}"
            jobs[job_id] = {"status": "pending"}
            
            # Start background thread
            thread = threading.Thread(
                target=background_magazine_job,
                args=(job_id, current_user.id, docx_paths, pdf_path, journal_meta, template_key)
            )
            thread.start()
            
            return jsonify({"status": "pending", "job_id": job_id})
            
        except Exception as e:
            print(f"[ERROR] {traceback.format_exc()}")
            return jsonify({"status": "error", "message": str(e)}), 500

    pdf_url = request.args.get("pdf_url")
    is_embedded = request.args.get("embed") == "1"
    histories = MagazineHistory.query.filter_by(user_id=current_user.id).order_by(MagazineHistory.id.desc()).all() if current_user.is_authenticated else []
    return render_template("index.html", pdf_url=pdf_url, template_names=TEMPLATE_NAMES, histories=histories, active_page='home', is_embedded=is_embedded)


@app.route("/history")
@login_required
def history():
    histories = MagazineHistory.query.filter_by(user_id=current_user.id).order_by(MagazineHistory.id.desc()).all()
    return render_template("history.html", histories=histories, active_page='history')


@app.route("/upload_pdf", methods=["POST"])
@login_required
def upload_pdf():
    file = request.files.get("pdf_file")
    if not file or not file.filename.lower().endswith(".pdf"):
        flash("Please upload a .pdf file!", "error")
        return redirect(url_for("editor"))
    
    try:
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        uid = str(uuid.uuid4())[:8]
        pdf_filename = f"{uid}_{file.filename}"
        pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], pdf_filename)
        file.save(pdf_path)
        
        new_history = MagazineHistory(
            user_id=current_user.id,
            title=file.filename,
            template="Tải Lên PDF",
            pdf_filename=pdf_filename
        )
        db.session.add(new_history)
        db.session.commit()
        
        flash("PDF uploaded successfully for flipbook view!", "success")
        pdf_url = url_for("static", filename=f"outputs/{pdf_filename}")
        return redirect(url_for("editor", pdf_url=pdf_url))
    except Exception as e:
        flash(f"PDF upload error: {str(e)}", "error")
        return redirect(url_for("editor"))


@app.route("/history/delete/<int:history_id>", methods=["POST"])
@login_required
def delete_history(history_id):
    history = MagazineHistory.query.filter_by(id=history_id, user_id=current_user.id).first()
    if history:
        # Delete PDF file if exists
        if history.pdf_filename:
            pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], history.pdf_filename)
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    print(f"[WARN] Không thể xóa file PDF: {e}")
        db.session.delete(history)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": "Đã xóa lịch sử tạp chí."})
        flash("Magazine history deleted.", "success")
    else:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Không tìm thấy lịch sử."}), 404
    return redirect(url_for("editor"))


try:
    setup_seepay_routes(app)
except (AssertionError, Exception) as e:
    print(f"[WARN] setup_seepay_routes loi: {e}")

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            current_user.name = name
        
        # Chỉ cho phép đổi mật khẩu với tài khoản local
        if current_user.auth_type == "local":
            old_password = request.form.get("old_password", "").strip()
            new_password = request.form.get("new_password", "").strip()
            confirm_password = request.form.get("confirm_password", "").strip()
            
            if old_password:
                if not check_password_hash(current_user.password, old_password):
                    flash("Current password incorrect!", "error")
                    return render_template("profile.html", active_page='profile')
                
                if not new_password or len(new_password) < 6:
                    flash("New password must be at least 6 characters!", "error")
                    return render_template("profile.html", active_page='profile')
                
                if new_password != confirm_password:
                    flash("New password confirmation does not match!", "error")
                    return render_template("profile.html", active_page='profile')
                
                current_user.password = generate_password_hash(new_password)
                flash("Password updated successfully.", "success")
        
        db.session.commit()
        flash("Personal information updated.", "success")
        return redirect(url_for("profile"))
        
    return render_template("profile.html", active_page='profile')

if __name__ == "__main__":
    app.run(debug=True, port=5000)
