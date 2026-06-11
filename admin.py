import os
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, abort, current_app
from flask_login import current_user, login_required
from flask_sqlalchemy import SQLAlchemy

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(f):
    """Decorator: chỉ cho phép user có role='admin'"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Vui lòng đăng nhập để tiếp tục.", "warning")
            return redirect(url_for("login"))
        if getattr(current_user, "role", "user") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated


def register_admin(app, db, User, Payment, MagazineHistory=None, SystemSetting=None, get_setting=None, set_setting=None):
    """Đăng ký admin blueprint và inject models"""
    
    # Use the provided model or try to get it from app context if needed
    # (But passing it as argument is safest)

    # ── Dashboard ──────────────────────────────────────────
    @admin_bp.route("/")
    @admin_required
    def dashboard():
        try:
            total_users    = User.query.count()
            total_payments = Payment.query.count()
            paid_payments  = Payment.query.filter_by(status="paid").count()
            total_revenue  = db.session.query(
                db.func.sum(Payment.amount)
            ).filter_by(status="paid").scalar() or 0
            
            total_magazines = MagazineHistory.query.count() if MagazineHistory else 0

            # 30-day stats
            since = datetime.utcnow() - timedelta(days=30)
            new_users_30d = User.query.filter(User.created_at >= since).count() if hasattr(User, "created_at") else 0
            revenue_30d   = db.session.query(
                db.func.sum(Payment.amount)
            ).filter(Payment.status == "paid", Payment.created_at >= since).scalar() or 0
            
            new_magazines_30d = MagazineHistory.query.filter(MagazineHistory.created_at >= since).count() if MagazineHistory else 0

            recent_payments = (
                Payment.query.order_by(Payment.id.desc()).limit(5).all()
            )
            recent_users = (
                User.query.order_by(User.id.desc()).limit(5).all()
            )
            recent_magazines = (
                MagazineHistory.query.order_by(MagazineHistory.id.desc()).limit(5).all() if MagazineHistory else []
            )

            # Pre-fetch user map for efficiency and safety
            u_ids = set([p.user_id for p in recent_payments] + [m.user_id for m in recent_magazines] + [u.id for u in recent_users])
            users_objs = User.query.filter(User.id.in_(u_ids)).all()
            user_map = {u.id: u for u in users_objs}

            stats = {
                "total_users":     total_users,
                "total_payments":  total_payments,
                "paid_payments":   paid_payments,
                "total_revenue":   total_revenue,
                "total_magazines": total_magazines,
                "new_users_30d":   new_users_30d,
                "revenue_30d":     revenue_30d,
                "new_mags_30d":    new_magazines_30d,
                "pending":         Payment.query.filter_by(status="pending").count(),
            }
            return render_template(
                "admin.html",
                page="dashboard",
                stats=stats,
                recent_payments=recent_payments,
                recent_users=recent_users,
                recent_magazines=recent_magazines,
                user_map=user_map
            )
        except Exception as e:
            print(f"[ADMIN ERROR] Dashboard: {e}")
            flash(f"Lỗi hệ thống: {str(e)}", "error")
            return render_template("admin.html", page="dashboard", stats={}, recent_payments=[], recent_users=[], recent_magazines=[], user_map={})

    # ── Users ───────────────────────────────────────────────
    @admin_bp.route("/users")
    @admin_required
    def users():
        q = request.args.get("q", "").strip()
        query = User.query
        if q:
            query = query.filter(
                db.or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%"))
            )
        users_list = query.order_by(User.id.desc()).all()
        return render_template("admin.html", page="users", users=users_list, q=q)

    @admin_bp.route("/users/<int:uid>/toggle-role", methods=["POST"])
    @admin_required
    def toggle_role(uid):
        user = User.query.get_or_404(uid)
        if user.id == current_user.id:
            return jsonify({"ok": False, "message": "Không thể đổi quyền của chính mình"})
        user.role = "user" if getattr(user, "role", "user") == "admin" else "admin"
        db.session.commit()
        return jsonify({"ok": True, "new_role": user.role})

    @admin_bp.route("/users/<int:uid>/toggle-active", methods=["POST"])
    @admin_required
    def toggle_active(uid):
        user = User.query.get_or_404(uid)
        if user.id == current_user.id:
            return jsonify({"ok": False, "message": "Không thể khoá chính mình"})
        user.is_active = 0 if getattr(user, "is_active", 1) else 1
        db.session.commit()
        return jsonify({"ok": True, "is_active": user.is_active})

    @admin_bp.route("/users/<int:uid>/delete", methods=["POST"])
    @admin_required
    def delete_user(uid):
        user = User.query.get_or_404(uid)
        if user.id == current_user.id:
            return jsonify({"ok": False, "message": "Không thể xóa tài khoản của chính mình!"})
        
        # Delete related data
        Payment.query.filter_by(user_id=uid).delete()
        if MagazineHistory:
            MagazineHistory.query.filter_by(user_id=uid).delete()
            
        db.session.delete(user)
        db.session.commit()
        return jsonify({"ok": True, "message": f"Đã xóa user {user.email}"})

    @admin_bp.route("/users/<int:uid>/details")
    @admin_required
    def user_details(uid):
        user = User.query.get_or_404(uid)
        payments = Payment.query.filter_by(user_id=uid).order_by(Payment.id.desc()).all()
        magazines = MagazineHistory.query.filter_by(user_id=uid).order_by(MagazineHistory.id.desc()).all() if MagazineHistory else []
        
        # Stats
        now = datetime.utcnow()
        since_week = now - timedelta(days=7)
        since_month = now - timedelta(days=30)
        
        week_paid = Payment.query.filter(Payment.user_id == uid, Payment.status == "paid", Payment.created_at >= since_week).count()
        month_paid = Payment.query.filter(Payment.user_id == uid, Payment.status == "paid", Payment.created_at >= since_month).count()
        
        return jsonify({
            "ok": True,
            "user": {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at.strftime("%d/%m/%Y %H:%M") if user.created_at else "N/A",
                "last_login": user.last_login.strftime("%d/%m/%Y %H:%M") if user.last_login else "N/A",
                "picture": user.picture
            },
            "stats": {
                "total_magazines": len(magazines),
                "total_payments": len(payments),
                "week_paid": week_paid,
                "month_paid": month_paid
            },
            "magazines": [{
                "id": m.id,
                "title": m.title or "Không tên",
                "pdf": m.pdf_filename,
                "date": m.created_at.strftime("%d/%m/%Y")
            } for m in magazines[:10]],
            "payments": [{
                "amount": p.amount,
                "status": p.status,
                "date": p.created_at.strftime("%d/%m/%Y")
            } for p in payments[:5]]
        })

    # ── Payments ────────────────────────────────────────────
    @admin_bp.route("/payments")
    @admin_required
    def payments():
        status_filter = request.args.get("status", "all")
        query = Payment.query
        if status_filter != "all":
            query = query.filter_by(status=status_filter)
        payments_list = query.order_by(Payment.id.desc()).all()
        user_map = {u.id: u for u in User.query.all()}
        return render_template(
            "admin.html",
            page="payments",
            payments=payments_list,
            user_map=user_map,
            status_filter=status_filter,
        )

    @admin_bp.route("/payments/<int:pid>/mark-paid", methods=["POST"])
    @admin_required
    def mark_paid(pid):
        p = Payment.query.get_or_404(pid)
        p.status  = "paid"
        p.paid_at = datetime.utcnow()
        db.session.commit()
        return jsonify({"ok": True})

    @admin_bp.route("/payments/<int:pid>/mark-pending", methods=["POST"])
    @admin_required
    def mark_pending(pid):
        p = Payment.query.get_or_404(pid)
        p.status  = "pending"
        p.paid_at = None
        db.session.commit()
        return jsonify({"ok": True})

    @admin_bp.route("/payments/<int:pid>/delete", methods=["POST"])
    @admin_required
    def delete_payment(pid):
        p = Payment.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        return jsonify({"ok": True})

    # ── Magazines ───────────────────────────────────────────
    @admin_bp.route("/magazines")
    @admin_required
    def magazines():
        if not MagazineHistory:
            flash("Tính năng quản lý tạp chí chưa khả dụng.", "warning")
            return redirect(url_for("admin.dashboard"))
            
        mags = MagazineHistory.query.order_by(MagazineHistory.id.desc()).all()
        user_map = {u.id: u for u in User.query.all()}
        return render_template(
            "admin.html",
            page="magazines",
            magazines=mags,
            user_map=user_map
        )

    @admin_bp.route("/magazines/<int:mid>/delete", methods=["POST"])
    @admin_required
    def delete_magazine(mid):
        if not MagazineHistory:
            return jsonify({"ok": False, "message": "Model not found"})
            
        mag = MagazineHistory.query.get_or_404(mid)
        try:
            # Delete physical file
            if mag.pdf_filename:
                pdf_path = os.path.join(current_app.config["UPLOAD_FOLDER"], mag.pdf_filename)
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
        except Exception as e:
            print(f"[ADMIN] Error deleting file: {e}")
            
        db.session.delete(mag)
        db.session.commit()
        return jsonify({"ok": True})

    # ── Settings ─────────────────────────────────────────────
    @admin_bp.route("/settings", methods=["GET", "POST"])
    @admin_required
    def settings():
        if request.method == "POST":
            # Lưu cấu hình
            llm_provider = request.form.get("llm_provider", "openrouter").strip()
            llm_model = request.form.get("llm_model", "openai/gpt-4o-mini").strip()
            openrouter_api_key = request.form.get("openrouter_api_key", "").strip()
            openai_api_key = request.form.get("openai_api_key", "").strip()
            deepseek_api_key = request.form.get("deepseek_api_key", "").strip()
            gemini_api_key = request.form.get("gemini_api_key", "").strip()
            
            pricing_per_magazine = request.form.get("pricing_per_magazine", "10000").replace(".", "").strip()
            pricing_packages = request.form.get("pricing_packages", "").strip()
            
            set_setting("llm_provider", llm_provider)
            set_setting("llm_model", llm_model)
            set_setting("openrouter_api_key", openrouter_api_key)
            set_setting("openai_api_key", openai_api_key)
            set_setting("deepseek_api_key", deepseek_api_key)
            set_setting("gemini_api_key", gemini_api_key)
            set_setting("pricing_per_magazine", pricing_per_magazine)
            
            if pricing_packages:
                import json
                try:
                    json.loads(pricing_packages)
                    set_setting("pricing_packages", pricing_packages)
                except ValueError:
                    flash("Chuỗi JSON Gói nạp tiền không hợp lệ! Vui lòng kiểm tra lại cấu trúc.", "error")
            
            flash("Cấu hình hệ thống đã được cập nhật thành công!", "success")
            return redirect(url_for("admin.settings"))
            
        # GET: Hiển thị các giá trị cấu hình hiện tại
        configs = {
            "llm_provider": get_setting("llm_provider", "openrouter"),
            "llm_model": get_setting("llm_model", "openai/gpt-4o-mini"),
            "openrouter_api_key": get_setting("openrouter_api_key", ""),
            "openai_api_key": get_setting("openai_api_key", ""),
            "deepseek_api_key": get_setting("deepseek_api_key", ""),
            "gemini_api_key": get_setting("gemini_api_key", ""),
            "pricing_per_magazine": get_setting("pricing_per_magazine", "10000"),
            "pricing_packages": get_setting("pricing_packages", "[]"),
        }
        return render_template("admin.html", page="settings", configs=configs)

    @admin_bp.route("/test-llm", methods=["POST"])
    @admin_required
    def test_llm():
        import requests
        data = request.json or {}
        provider = data.get("llm_provider", "openrouter").strip()
        model = data.get("llm_model", "").strip()
        api_key = data.get("api_key", "").strip()

        if not api_key:
            return jsonify({"ok": False, "message": "Vui lòng nhập API Key trước khi test!"})

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}", 
            "Content-Type": "application/json"
        }
        model_to_use = model or "openai/gpt-4o-mini"

        if provider == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            model_to_use = model or "gpt-4o-mini"
        elif provider == "deepseek":
            url = "https://api.deepseek.com/beta/chat/completions"
            model_to_use = model or "deepseek-chat"
        elif provider == "gemini":
            url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
            model_to_use = model or "gemini-2.0-flash"

        try:
            payload = {
                "model": model_to_use,
                "messages": [
                    {"role": "user", "content": "Hi"}
                ],
                "max_tokens": 150
            }
            # Timeout ngắn 15s để phản hồi nhanh chóng
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if resp.status_code == 200:
                res_data = resp.json()
                choices = res_data.get("choices")
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    ai_response = message.get("content")
                    if ai_response:
                        return jsonify({
                            "ok": True,
                            "message": ai_response.strip()
                        })
                return jsonify({
                    "ok": False,
                    "message": f"Empty response or invalid JSON: {resp.text}"
                })
            else:
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", resp.text)
                except Exception:
                    error_msg = resp.text
                return jsonify({
                    "ok": False,
                    "message": f"HTTP {resp.status_code}: {error_msg}"
                })
        except Exception as e:
            return jsonify({
                "ok": False,
                "message": f"Connection failed: {str(e)}"
            })

    # ── Personal Features (Like User) ────────────────────────
    @admin_bp.route("/my-editor")
    @admin_required
    def my_editor():
        histories = MagazineHistory.query.filter_by(user_id=current_user.id).order_by(MagazineHistory.id.desc()).all() if MagazineHistory else []
        return render_template("admin.html", page="my_editor", histories=histories)

    @admin_bp.route("/my-history")
    @admin_required
    def my_history():
        histories = MagazineHistory.query.filter_by(user_id=current_user.id).order_by(MagazineHistory.id.desc()).all() if MagazineHistory else []
        return render_template("admin.html", page="my_history", histories=histories)

    @admin_bp.route("/my-billing", methods=["GET", "POST"])
    @admin_required
    def my_billing():
        if request.method == "POST":
            import uuid
            raw_amount = request.form.get("amount", "0").strip()
            try: amount = int(raw_amount)
            except ValueError: amount = 0
            if amount < 10000:
                flash("Số tiền tối thiểu là 10,000 VND.", "error")
                return redirect(url_for("admin.my_billing"))
            code = f"ADM{uuid.uuid4().hex[:8].upper()}"
            payment = Payment(user_id=current_user.id, payment_code=code, amount=amount, transfer_content=code)
            db.session.add(payment)
            db.session.commit()
            flash("Đã tạo yêu cầu thanh toán.", "success")
            return redirect(url_for("admin.my_billing", code=code))
        
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
        return render_template("admin.html", 
            page="my_billing", 
            payment=current_payment, 
            recent_payments=recent_payments,
            time_left=time_left,
            sepay_bank_name=os.getenv("SEPAY_BANK_NAME", "MB Bank"),
            sepay_account_number=os.getenv("SEPAY_ACCOUNT_NUMBER", ""),
            sepay_account_name=os.getenv("SEPAY_ACCOUNT_NAME", "AI Tap Chi")
        )

    @admin_bp.route("/my-profile")
    @admin_required
    def my_profile():
        return render_template("admin.html", page="my_profile")

    # ── Chart Data API ────────────────────────────────────────
    @admin_bp.route("/api/chart-data")
    @admin_required
    def get_chart_data():
        try:
            # Lấy thống kê 30 ngày gần đây
            now = datetime.utcnow()
            dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]
            
            # Khởi tạo map cho các ngày
            users_count = {d: 0 for d in dates}
            revenue_count = {d: 0 for d in dates}
            magazines_count = {d: 0 for d in dates}
            
            since = now - timedelta(days=30)
            
            # 1. Thống kê user mới theo ngày
            if hasattr(User, "created_at"):
                users_data = db.session.query(
                    db.func.date(User.created_at),
                    db.func.count(User.id)
                ).filter(User.created_at >= since).group_by(db.func.date(User.created_at)).all()
                
                for d_val, count in users_data:
                    d_str = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
                    if d_str in users_count:
                        users_count[d_str] = count
                        
            # 2. Thống kê doanh thu theo ngày
            revenue_data = db.session.query(
                db.func.date(Payment.created_at),
                db.func.sum(Payment.amount)
            ).filter(Payment.status == "paid", Payment.created_at >= since).group_by(db.func.date(Payment.created_at)).all()
            
            for d_val, amount_sum in revenue_data:
                d_str = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
                if d_str in revenue_count:
                    revenue_count[d_str] = int(amount_sum or 0)
                    
            # 3. Thống kê tạp chí theo ngày
            if MagazineHistory:
                mags_data = db.session.query(
                    db.func.date(MagazineHistory.created_at),
                    db.func.count(MagazineHistory.id)
                ).filter(MagazineHistory.created_at >= since).group_by(db.func.date(MagazineHistory.created_at)).all()
                
                for d_val, count in mags_data:
                    d_str = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
                    if d_str in magazines_count:
                        magazines_count[d_str] = count
            
            # 4. Thống kê đơn hàng thành công theo ngày
            payments_count = {d: 0 for d in dates}
            paid_data = db.session.query(
                db.func.date(Payment.paid_at),
                db.func.count(Payment.id)
            ).filter(Payment.status == "paid", Payment.paid_at >= since).group_by(db.func.date(Payment.paid_at)).all()

            for d_val, count in paid_data:
                d_str = d_val.strftime("%Y-%m-%d") if hasattr(d_val, "strftime") else str(d_val)
                if d_str in payments_count:
                    payments_count[d_str] = count

            # Format label ngày dạng DD/MM cho dễ nhìn ở UI
            formatted_labels = []
            for d in dates:
                parts = d.split('-')
                formatted_labels.append(f"{parts[2]}/{parts[1]}")
                
            return jsonify({
                "success": True,
                "labels": formatted_labels,
                "users": [users_count[d] for d in dates],
                "revenue": [revenue_count[d] for d in dates],
                "magazines": [magazines_count[d] for d in dates],
                "payments": [payments_count[d] for d in dates]
            })
        except Exception as e:
            print(f"[ADMIN CHART ERROR]: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    # ── Notification API ──────────────────────────────────────
    @admin_bp.route("/api/notifications")
    @admin_required
    def get_notifications():
        try:
            lang = request.cookies.get("lang", "en")
            is_en = lang == "en"
            notifications = []
            
            # 1. Tài khoản được tạo (New accounts in the last 30 days)
            users = User.query.order_by(User.id.desc()).limit(10).all()
            for u in users:
                title = "New account" if is_en else "Tài khoản mới"
                name_val = u.name or ("No name" if is_en else "Không tên")
                content = f"Account <strong>{u.email}</strong> ({name_val}) has been registered successfully." if is_en else f"Tài khoản <strong>{u.email}</strong> ({name_val}) đã được đăng ký thành công."
                notifications.append({
                    "type": "user_registered",
                    "title": title,
                    "content": content,
                    "time": u.created_at.isoformat() if u.created_at else None,
                    "icon": "fa-user-plus",
                    "icon_color": "blue"
                })
                
            # 2. Tài khoản được tạo yêu cầu nạp tiền (Pending payments)
            pending_payments = Payment.query.filter_by(status="pending").order_by(Payment.id.desc()).limit(10).all()
            u_ids = list(set([p.user_id for p in pending_payments]))
            users_map = {u.id: u for u in User.query.filter(User.id.in_(u_ids)).all()} if u_ids else {}
            for p in pending_payments:
                u = users_map.get(p.user_id)
                email = u.email if u else f"User #{p.user_id}"
                title = "Top-up request" if is_en else "Yêu cầu nạp tiền"
                content = f"Account <strong>{email}</strong> has requested to top up <strong><span class=\"currency-convert\" data-balance=\"{p.amount}\">{p.amount:,} VND</span></strong> (Tx ID: <code>{p.payment_code}</code>)." if is_en else f"Tài khoản <strong>{email}</strong> vừa tạo yêu cầu nạp <strong><span class=\"currency-convert\" data-balance=\"{p.amount}\">{p.amount:,} VND</span></strong> (Mã GD: <code>{p.payment_code}</code>)."
                notifications.append({
                    "type": "payment_pending",
                    "title": title,
                    "content": content,
                    "time": p.created_at.isoformat() if p.created_at else None,
                    "icon": "fa-wallet",
                    "icon_color": "warning"
                })
                
            # 3. Tài khoản nạp tiền thành công và số tiền (Paid payments)
            paid_payments = Payment.query.filter_by(status="paid").order_by(Payment.id.desc()).limit(10).all()
            u_ids_paid = list(set([p.user_id for p in paid_payments]))
            users_map_paid = {u.id: u for u in User.query.filter(User.id.in_(u_ids_paid)).all()} if u_ids_paid else {}
            for p in paid_payments:
                u = users_map_paid.get(p.user_id)
                email = u.email if u else f"User #{p.user_id}"
                title = "Top-up successful" if is_en else "Nạp tiền thành công"
                content = f"Account <strong>{email}</strong> has successfully topped up <strong><span class=\"currency-convert\" data-balance=\"{p.amount}\">{p.amount:,} VND</span></strong>." if is_en else f"Tài khoản <strong>{email}</strong> đã nạp thành công <strong><span class=\"currency-convert\" data-balance=\"{p.amount}\">{p.amount:,} VND</span></strong>."
                notifications.append({
                    "type": "payment_paid",
                    "title": title,
                    "content": content,
                    "time": p.paid_at.isoformat() if p.paid_at else p.created_at.isoformat(),
                    "icon": "fa-check-circle",
                    "icon_color": "success"
                })
                
            # Sắp xếp theo thời gian mới nhất
            notifications.sort(key=lambda x: x["time"] or "", reverse=True)
            
            return jsonify({
                "success": True,
                "notifications": notifications[:15]
            })
        except Exception as e:
            print(f"[ADMIN NOTIFICATIONS ERROR]: {e}")
            return jsonify({"success": False, "message": str(e)}), 500

    app.register_blueprint(admin_bp)
    return admin_bp