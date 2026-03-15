import csv
import io
import json
import numpy as np
import tensorflow as tf
from flask import Flask, render_template, redirect, request, abort, session, Response, jsonify
from data import db_session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from data.users import User
from forms.user import RegisterForm, LoginForm
import os
from functools import wraps
from collections import defaultdict
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = '65432456uijhgfdsxcvbn'


MODEL_PATH = 'alien_signals_model.h5'
MAP_PATH = 'classes_map.json'
ANALYTICS_PATH = 'analytics.json'

model = None
class_map = {}

if os.path.exists(MODEL_PATH):
    model = tf.keras.models.load_model(MODEL_PATH)
if os.path.exists(MAP_PATH):
    with open(MAP_PATH, 'r', encoding='utf-8') as f:
        class_map = json.load(f)
inv_map = {v: k for k, v in class_map.items()}


def predict_signal(file_path):
    try:
        data = np.load(file_path, allow_pickle=True)
        x_test = data['test_x'][0]

        target_frames, fft_size = 64, 64
        s = np.array(x_test).flatten()
        needed_len = target_frames * fft_size
        s = np.pad(s, (0, max(0, needed_len - len(s))))[:needed_len]
        spec = np.abs(np.fft.rfft(s.reshape(target_frames, fft_size), axis=1))
        spec = np.log(spec + 1e-7)
        spec = (spec - np.mean(spec)) / (np.std(spec) + 1e-7)

        input_data = np.expand_dims(spec, axis=(0, -1))
        preds = model.predict(input_data)
        idx = np.argmax(preds)
        return inv_map.get(idx, "Unknown"), float(np.max(preds))
    except Exception as e:
        return str(e), 0


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


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        if not current_user.is_authenticated:
            return redirect('/login')

        file = request.files.get("file")
        if not file or file.filename == "":
            abort(400, "Файл не выбран")

        # Сохранение
        os.makedirs(f"audio/audio_{current_user.id}", exist_ok=True)
        file_path = os.path.join(f"audio/audio_{current_user.id}", "file.npz")
        file.save(file_path)


        res_planet, res_conf = predict_signal(file_path)
        session['last_prediction'] = {"planet": res_planet, "conf": round(res_conf * 100, 2)}

        return redirect('/statistics')
    return render_template('index.html')


@app.route('/statistics')
@login_required
def statistic():
    if os.path.exists(ANALYTICS_PATH):
        with open(ANALYTICS_PATH, 'r', encoding='utf-8') as f:
            analytics_data = json.load(f)
    else:
        analytics_data = {}

    prediction = session.get('last_prediction', None)
    return render_template('statistics.html', data=analytics_data, prediction=prediction)


@app.route('/favicon.ico')
def favicon():
    return '', 204


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
        user=user
    )


@app.route('/admin', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin():
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
    return render_template("admin_first.html", form=form)


if __name__ == '__main__':
    app.run(port=8080, host='127.0.0.1', debug=True)