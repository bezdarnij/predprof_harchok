import csv
import io

from flask import Flask, render_template, redirect, request, abort, session, Response
from data import db_session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from data.users import User
from data.tasks import Tasks
from data.submissions import Submissions
from data.task_tests import TaskTest
from forms.user import RegisterForm, LoginForm
from flask_socketio import SocketIO, join_room, leave_room, emit
import uuid
import os
import subprocess

from functools import wraps
from collections import defaultdict
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = '65432456uijhgfdsxcvbn'

login_manager = LoginManager()
login_manager.init_app(app)



@login_manager.user_loader
def load_user(user_id):
    db_sess = db_session.create_session()
    return db_sess.get(User, user_id)


db_session.global_init("db/task.db")


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.admin:
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def user_ban(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_user.ban:
            abort(403)
        return func(*args, **kwargs)

    return wrapper


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route("/")
@user_ban
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def reqister():
    form = RegisterForm()
    if form.validate_on_submit():
        if form.password.data != form.password_again.data:
            return render_template('register.html', title='Регистрация',
                                   form=form,
                                   message="Пароли не совпадают")
        db_sess = db_session.create_session()
        if db_sess.query(User).filter(User.email == form.email.data).first():
            return render_template('register.html', title='Регистрация',
                                   form=form,
                                   message="Такой пользователь уже есть")
        user = User(
            name=form.name.data,
            email=form.email.data,
        )
        user.set_password(form.password.data)
        db_sess.add(user)
        db_sess.commit()
        return redirect('/login')
    return render_template('register.html', title='Регистрация', form=form)


@app.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        db_sess = db_session.create_session()
        user = db_sess.query(User).filter(User.email == form.email.data).first()
        if user and user.check_password(form.password.data) and user.ban != 1:
            login_user(user, remember=form.remember_me.data)
            return redirect("/")
        return render_template('login.html',
                               message="Неправильный логин или пароль или вы в бане",
                               form=form)
    return render_template('login.html', title='Авторизация', form=form)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect("/")


@app.route('/profile')
@app.route('/profile/<int:user_id>')
@app.route('/admin/profile/<int:user_id>')
@login_required
@user_ban
def profile(user_id=None):
    db_sess = db_session.create_session()
    if user_id is None:
        user = current_user
    else:
        user = db_sess.get(User, user_id)
        if not user:
            abort(404)
    return render_template(
        'profile.html',
    )


@app.route('/edit/profile', methods=['GET', 'POST'])
@app.route('/edit/profile/<int:user_id>', methods=['GET', 'POST'])
@login_required
@user_ban
def edit_profile(user_id=None):
    subject = session.get('subject')
    db_sess = db_session.create_session()
    if user_id is None:
        user = current_user
    else:
        user = db_sess.get(User, user_id)
        if not user:
            abort(404)
        if user.id != current_user.id and not current_user.admin:
            abort(403)

    message = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')

        if not name or not email:
            message = "Имя и почта обязательны"
        elif db_sess.query(User).filter(User.email == email, User.id != user.id).first():
            message = "Эта почта уже используется"
        elif password or password_confirm:
            if password != password_confirm:
                message = "Пароли не совпадают"
            else:
                user.set_password(password)

        if message is None:
            user.name = name
            user.email = email
            db_sess.commit()
            return redirect(f"/profile/{user.id}")

    return render_template(
        'edit_profile.html',
        user=user,
        subject=subject,
        message=message
    )


@app.route('/admin', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin():
    subject = session.get('subject')
    db_sess = db_session.create_session()
    users = db_sess.query(User)
    if request.method == "POST":
        for user in users:
            admin_value = request.form.get(f"admin_{user.id}")
            ban_value = request.form.get(f"ban_{user.id}")
            if admin_value == "admin":
                user.admin = 1
            if admin_value == "user":
                user.admin = 0
            if ban_value == "banned":
                user.ban = 1
            if ban_value == "unbanned":
                user.ban = 0
        db_sess.commit()
        return redirect('/admin')
    return render_template("admin_first.html", users=users, subject=subject)


if __name__ == '__main__':
    socketio.run(app, port=8080, host='127.0.0.1', allow_unsafe_werkzeug=True, debug=True)
