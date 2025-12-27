import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from functools import wraps

auth_bp = Blueprint('auth', __name__)

def get_admin_creds():
    return (
        os.getenv('ADMIN_USER', 'admin'),
        os.getenv('ADMIN_PASS', ''),
    )

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
        admin_user, admin_pass = get_admin_creds()

        if username == admin_user and password == admin_pass:
            session.permanent = True
            session['user'] = admin_user
            nxt = request.args.get('next') or url_for('main.index')
            return redirect(nxt)
        else:
            flash('Sai tài khoản hoặc mật khẩu', 'danger')

    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
