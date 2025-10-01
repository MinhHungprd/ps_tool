import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps
from werkzeug.security import check_password_hash  # NEW

auth_bp = Blueprint('auth', __name__)

# ==== Admin qua ENV (giữ nguyên) ====
def get_admin_creds():
    return (
        os.getenv('ADMIN_USER', 'admin'),
        os.getenv('ADMIN_PASS', ''),
    )

# ==== User file: nhiều tài khoản, plain hoặc hash ====
USER_FILE = os.getenv('USER_FILE', 'storage/user/user.txt')

def _load_users_from_file():
    """
    Trả về dict {username: password_or_hash}
    - Bỏ qua dòng trống / comment (#...)
    - Đọc 'username:password' (chỉ tách theo dấu ':' đầu tiên)
    """
    users = {}
    if not os.path.isfile(USER_FILE):
        return users
    with open(USER_FILE, 'r', encoding='utf-8') as f:
        for raw in f.read().splitlines():
            line = (raw or '').strip()
            if not line or line.startswith('#'):
                continue
            if ':' not in line:
                continue
            u, p = line.split(':', 1)
            u = u.strip()
            p = p.strip()
            if u:
                users[u] = p
    return users

def _verify_user_password(stored: str, provided: str) -> bool:
    """
    stored: có thể là plain hoặc hash (werkzeug)
    - Nếu có dạng hash (thường chứa 'pbkdf2:' hoặc có nhiều dấu '$'), dùng check_password_hash
    - Ngược lại, so sánh plain
    """
    if not isinstance(stored, str):
        return False
    # heuristics: nếu có dấu '$' hoặc 'pbkdf2:' → coi như hash
    if '$' in stored or stored.startswith('pbkdf2:'):
        try:
            return check_password_hash(stored, provided)
        except Exception:
            return False
    return stored == provided

def get_user_from_file(username: str, password: str) -> bool:
    """
    Kiểm tra username/password trong user.txt.
    Trả True nếu hợp lệ.
    """
    users = _load_users_from_file()
    if not users:
        return False
    stored = users.get(username)
    if stored is None:
        return False
    return _verify_user_password(stored, password)

# ==== Decorator giữ nguyên ====
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('auth.login', next=request.path))
        return view(*args, **kwargs)
    return wrapped

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        # 1) Ưu tiên admin qua ENV (giữ hành vi cũ)
        admin_user, admin_pass = get_admin_creds()
        if username == admin_user and password == admin_pass:
            session.permanent = True
            session['user'] = admin_user
            nxt = request.args.get('next') or url_for('main.index')
            return redirect(nxt)

        # 2) Nếu không phải admin ENV → thử user.txt
        if get_user_from_file(username, password):
            session.permanent = True
            session['user'] = username
            nxt = request.args.get('next') or url_for('main.index')
            return redirect(nxt)

        flash('Sai tài khoản hoặc mật khẩu', 'danger')

    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
