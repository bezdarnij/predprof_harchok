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
from elo import update_elo
from ai import generate_task
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

socketio = SocketIO(app, cors_allowed_origins="*")
matches = {}

subjects = ['информатика', 'математика', 'физика', 'химия', 'биология', 'литература', 'история', 'география',
            'английский язык', 'русский язык']


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@app.errorhandler(401)
def unauthorized(e):
    return render_template("401.html"), 401


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
@app.route("/<subject>/choice", methods=['GET', 'POST'])
def subject(subject='subject'):
    session['subject'] = 'subject'
    if subject != 'subject':
        session['subject'] = subject
        return redirect(f"/{subject}/")
    return render_template('subject.html', subject=subject)


@app.route("/<subject>/")
@login_required
@user_ban
def index(subject=None):
    if subject not in subjects:
        abort(404)
    session['subject'] = subject
    if request.path == '/' or 'subject' in request.path:
        return redirect('/subject/choice')
    db_sess = db_session.create_session()
    sort_by = request.args.get('sort_by')

    difficulties = db_sess.query(Tasks.difficulty).filter(Tasks.subject == subject).distinct().all()
    difficulties = [d[0] for d in difficulties if d[0]]
    themes = db_sess.query(Tasks.theme).filter(Tasks.subject == subject).distinct().all()
    themes = [t[0] for t in themes if t[0]]
    selected_difficulties = request.args.getlist('difficulty')
    selected_themes = request.args.getlist('theme')

    query = db_sess.query(Tasks).filter(Tasks.subject == subject)
    if selected_difficulties:
        query = query.filter(Tasks.difficulty.in_(selected_difficulties))
    if selected_themes:
        query = query.filter(Tasks.theme.in_(selected_themes))
    if sort_by == 'difficulty':
        tasks = query.order_by(Tasks.difficulty).all()
    elif sort_by == 'theme':
        tasks = query.all()
    else:
        tasks = query.all()
    submissions = db_sess.query(Submissions)
    return render_template('tasks.html', tasks=tasks, subject=subject,
                           difficulties=difficulties, themes=themes, selected_difficulties=selected_difficulties,
                           selected_themes=selected_themes, sort_by=sort_by, Submissions=Submissions,
                           submissions=submissions)


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
    subject = session.get('subject')
    db_sess = db_session.create_session()
    if user_id is None:
        user = current_user
    else:
        user = db_sess.get(User, user_id)
        if not user:
            abort(404)

    all_submissions = db_sess.query(Submissions).filter(
        Submissions.user_id == user.id
    ).order_by(Submissions.created_at.desc()).all()
    tasks_stats = {}
    for submission in all_submissions:
        task_id = submission.task_id
        task_task = db_sess.get(Tasks, task_id)
        if task_id not in tasks_stats:
            tasks_stats[task_id] = {
                'task': submission.tasks,
                'best_submission': submission,
                'attempts': 1,
                'solved': submission.verdict == "OK",
                'subject': task_task.subject
            }
        else:
            tasks_stats[task_id]['attempts'] += 1
            if task_task.subject == 'информатика':
                if submission.total_tests > tasks_stats[task_id]['best_submission'].total_tests:
                    tasks_stats[task_id]['best_submission'] = submission
            if submission.verdict == "OK":
                tasks_stats[task_id]['solved'] = True
    tasks_stats = tasks_stats.values()
    total_tasks_attempted = len(tasks_stats)
    solved_tasks = sum(1 for t in tasks_stats if t['solved'])
    total_submissions = len(all_submissions)

    return render_template(
        'profile.html',
        user=user,
        tasks_stats=tasks_stats,
        total_tasks_attempted=total_tasks_attempted,
        solved_tasks=solved_tasks,
        total_submissions=total_submissions,
        is_own_profile=(user.id == current_user.id),
        subject=subject
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


@app.route('/analytics')
@login_required
@user_ban
def analytics():
    subject = session.get('subject')
    db_sess = db_session.create_session()
    submissions = db_sess.query(Submissions).join(Tasks).all()

    total_submissions = len(submissions)
    ok_submissions = sum(1 for s in submissions if s.verdict == "OK")
    accuracy = (ok_submissions / total_submissions * 100) if total_submissions else 0

    task_attempts = defaultdict(list)
    theme_stats = {}
    solve_times = []

    for s in submissions:
        task_attempts[(s.user_id, s.task_id)].append(s)

    for (user_id, task_id), subs in task_attempts.items():
        subs_sorted = sorted(subs, key=lambda x: x.created_at or datetime.min)
        first_time = subs_sorted[0].created_at
        ok_time = None
        for s in subs_sorted:
            if s.verdict == "OK":
                ok_time = s.created_at
                break
        if first_time and ok_time and ok_time >= first_time:
            solve_times.append((ok_time - first_time).total_seconds())

        task = subs_sorted[0].tasks
        theme = task.theme if task and task.theme else "Без темы"
        if theme not in theme_stats:
            theme_stats[theme] = {"total": 0, "ok": 0, "solve_times": []}
        theme_stats[theme]["total"] += len(subs_sorted)
        theme_stats[theme]["ok"] += sum(1 for s in subs_sorted if s.verdict == "OK")
        if first_time and ok_time and ok_time >= first_time:
            theme_stats[theme]["solve_times"].append((ok_time - first_time).total_seconds())

    avg_solve_time = sum(solve_times) / len(solve_times) if solve_times else None

    theme_rows = []
    max_theme_total = max((v["total"] for v in theme_stats.values()), default=0)
    for theme, data in sorted(theme_stats.items(), key=lambda x: x[0]):
        theme_accuracy = (data["ok"] / data["total"] * 100) if data["total"] else 0
        theme_avg_time = (sum(data["solve_times"]) / len(data["solve_times"])
                          if data["solve_times"] else None)
        theme_rows.append({
            "theme": theme,
            "total": data["total"],
            "ok": data["ok"],
            "accuracy": theme_accuracy,
            "avg_time": theme_avg_time,
            "bar": (data["total"] / max_theme_total * 100) if max_theme_total else 0
        })

    return render_template(
        "analytics.html",
        subject=subject,
        total_submissions=total_submissions,
        ok_submissions=ok_submissions,
        accuracy=accuracy,
        avg_solve_time=avg_solve_time,
        theme_rows=theme_rows
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


@app.route('/admin/competitions', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_competitions():
    subject = session.get('subject')
    db_sess = db_session.create_session()
    message = None
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        room = request.form.get("room")
        if not room or room not in matches:
            error = "Комната не найдена"
        else:
            if action == "finish":
                if matches[room].get("finished"):
                    message = "Матч уже завершен"
                elif len(matches[room].get("players", [])) < 2:
                    error = "Недостаточно игроков для завершения"
                else:
                    result = finish_match(room)
                    message = f"Матч завершен: {result}"
            elif action == "cancel":
                matches.pop(room, None)
                message = "Комната удалена"
            else:
                error = "Неизвестное действие"

    rooms = []
    for room_id, info in matches.items():
        players = []
        for uid in info.get('players', []):
            user = db_sess.get(User, int(uid))
            players.append(user.name if user else f"#{uid}")
        rooms.append({
            "room": room_id,
            "subject": info.get("subject"),
            "players": players,
            "player_count": len(info.get("players", [])),
            "finished": info.get("finished", False),
            "result": info.get("result"),
            "task_id": info.get("task_id")
        })

    return render_template(
        "admin_competitions.html",
        subject=subject,
        rooms=rooms,
        message=message,
        error=error
    )


@app.route('/admin/results', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_results():
    subject = session.get('subject')
    db_sess = db_session.create_session()
    message = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "delete":
            submission_id = request.form.get("submission_id")
            if submission_id and submission_id.isdigit():
                submission = db_sess.get(Submissions, int(submission_id))
                if submission:
                    db_sess.delete(submission)
                    db_sess.commit()
                    message = "Результат удален"

    user_id = request.args.get("user_id", "").strip()
    task_id = request.args.get("task_id", "").strip()
    verdict = request.args.get("verdict", "").strip()
    limit_raw = request.args.get("limit", "").strip()

    query = db_sess.query(Submissions).order_by(Submissions.created_at.desc())
    if user_id.isdigit():
        query = query.filter(Submissions.user_id == int(user_id))
    if task_id.isdigit():
        query = query.filter(Submissions.task_id == int(task_id))
    if verdict:
        query = query.filter(Submissions.verdict == verdict)

    limit = 200
    if limit_raw.isdigit():
        limit = min(int(limit_raw), 1000)

    submissions = query.limit(limit).all()

    return render_template(
        "admin_results.html",
        subject=subject,
        submissions=submissions,
        message=message,
        user_id=user_id,
        task_id=task_id,
        verdict=verdict,
        limit=limit
    )


@app.route('/admin/task', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def subject_admin():
    subject = session['subject']
    return render_template("subject_admin.html", subject=subject)


@app.route('/admin/task/<subject_admin>', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_task(subject_admin):
    subject = session['subject']
    if subject_admin == 'информатика':
        if request.method == "POST":
            db_sess = db_session.create_session()
            task_name = request.form.get("task_name")
            memory_limit = request.form.get("memory_limit")
            time_limit = request.form.get("time_limit")
            task_description = request.form.get("task_description")
            input_data = request.form.get("input_data")
            output_data = request.form.get("output_data")
            level = request.form.get("level")
            theme = request.form.get("theme")
            test_list = []
            test_list.append((request.form.get("test1_input"), request.form.get("test1_output")))
            test_list.append((request.form.get("test2_input"), request.form.get("test2_output")))
            test_list.append((request.form.get("test3_input"), request.form.get("test3_output")))
            test_list.append((request.form.get("test4_input"), request.form.get("test4_output")))
            test_list.append((request.form.get("test5_input"), request.form.get("test5_output")))
            task = Tasks(
                subject=subject_admin,
                title=task_name,
                statement=task_description,
                input_format=input_data,
                output_format=output_data,
                memory_limit=memory_limit,
                time_limit=time_limit,
                difficulty=level,
                theme=theme
            )
            task_id = db_sess.query(Tasks).all()[-1].id + 1
            for i in range(5):
                task_test = TaskTest(
                    task_id=task_id,
                    input_data=test_list[i][0],
                    output=test_list[i][1],
                )
                db_sess.add(task_test)
            db_sess.add(task)
            db_sess.commit()
    else:
        if request.method == "POST":
            db_sess = db_session.create_session()
            task_name = request.form.get("task_name")
            task_description = request.form.get("task_description")
            level = request.form.get("level")
            theme = request.form.get("theme")
            task = Tasks(
                subject=subject_admin,
                title=task_name,
                statement=task_description,
                difficulty=level,
                theme=theme
            )
            task_id = db_sess.query(Tasks).all()[-1].id + 1
            task_test = TaskTest(
                task_id=task_id,
                input_data=request.form.get("test_input"),
            )
            db_sess.add(task_test)
            db_sess.add(task)
            db_sess.commit()
    return render_template("admin_task.html", subject_admin=subject_admin, subject=subject)


@app.route('/admin/task_list', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_task_list():
    subject = session['subject']
    db_sess = db_session.create_session()
    tasks = db_sess.query(Tasks).all()
    if request.method == "POST":
        file = request.files.get("file")
        if not file or file.filename == "":
            abort(400, "Файл не выбран")
        file.filename = "task.csv"
        os.makedirs(f"task", exist_ok=True)
        file.save(os.path.join(f"task", file.filename))
        with open('task/task.csv', 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=',')
            h = next(reader)
            for row in reader:
                task = Tasks(
                    subject=row[1],
                    theme=row[2],
                    difficulty=row[3],
                    title=row[4],
                    statement=row[5],
                    input_format=row[6],
                    output_format=row[7],
                    time_limit=row[8],
                    memory_limit=row[9]
                )
                db_sess.add(task)
                db_sess.commit()
        return redirect('/admin/task_list')

    return render_template("task_list.html", subject=subject, tasks=tasks)


@app.route('/admin/task_ai/<subject_admin>', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def select_difficulty(subject_admin):
    subject = session['subject']
    return render_template("select_difficulty.html", subject_admin=subject_admin, subject=subject)


@app.route('/admin/task_ai/<subject_admin>/<difficulty>', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def ai(subject_admin, difficulty):
    if subject_admin not in subjects:
        abort(404)
    subject = session['subject']
    ai_subject = subject_admin
    ai_difficulty = difficulty
    ai_task = generate_task(ai_difficulty, ai_subject)
    while ai_task.get("error") is not None:
        ai_task = generate_task(ai_difficulty, subject)
    ai_theme = ai_task.get("тема")
    ai_task_name = ai_task.get("название задачи")
    ai_task_description = ai_task.get("условие задачи")
    ai_level = ai_difficulty
    ai_memory_limit = ai_task.get("лимит памяти")
    ai_time_limit = ai_task.get("лимит времени")
    ai_input_data = ai_task.get("входные данные")
    ai_output_data = ai_task.get("выходные данные")
    ai_test = []
    if subject_admin == 'информатика':
        for i in range(1, 6):
            ai_test.append((ai_task.get(f"входные данные тест {i}"), ai_task.get(f"выходные данные тест {i}")))
    else:
        ai_test.append((ai_task.get("ответ"), ""))
    if subject_admin == 'информатика':
        if request.method == "POST":
            db_sess = db_session.create_session()
            task_name = request.form.get("task_name")
            memory_limit = request.form.get("memory_limit")
            time_limit = request.form.get("time_limit")
            task_description = request.form.get("task_description")
            input_data = request.form.get("input_data")
            output_data = request.form.get("output_data")
            theme = request.form.get("theme")
            test_list = []
            test_list.append((request.form.get("test1_input"), request.form.get("test1_output")))
            test_list.append((request.form.get("test2_input"), request.form.get("test2_output")))
            test_list.append((request.form.get("test3_input"), request.form.get("test3_output")))
            test_list.append((request.form.get("test4_input"), request.form.get("test4_output")))
            test_list.append((request.form.get("test5_input"), request.form.get("test5_output")))
            task = Tasks(
                subject=subject_admin,
                title=task_name,
                statement=task_description,
                input_format=input_data,
                output_format=output_data,
                memory_limit=memory_limit,
                time_limit=time_limit,
                difficulty=ai_level,
                theme=theme
            )
            task_id = db_sess.query(Tasks).all()[-1].id + 1
            for i in range(5):
                task_test = TaskTest(
                    task_id=task_id,
                    input_data=test_list[i][0],
                    output=test_list[i][1],
                )
                db_sess.add(task_test)
            db_sess.add(task)
            db_sess.commit()
            return redirect('/admin')
    else:
        if request.method == "POST":
            db_sess = db_session.create_session()
            task_name = request.form.get("task_name")
            task_description = request.form.get("task_description")
            theme = request.form.get("theme")
            task = Tasks(
                subject=subject_admin,
                title=task_name,
                statement=task_description,
                difficulty=ai_level,
                theme=theme
            )
            task_id = db_sess.query(Tasks).all()[-1].id + 1
            task_test = TaskTest(
                task_id=task_id,
                input_data=request.form.get("test_input"),
            )
            db_sess.add(task_test)
            db_sess.add(task)
            db_sess.commit()
            return redirect('/admin')
    return render_template("admin_task_ai.html", subject=subject, ai_task_name=ai_task_name,
                           ai_memory_limit=ai_memory_limit, ai_time_limit=ai_time_limit,
                           ai_task_description=ai_task_description, ai_input_data=ai_input_data,
                           ai_output_data=ai_output_data, ai_level=ai_level, ai_theme=ai_theme,
                           ai_subject=ai_subject, ai_test=ai_test, subject_admin=subject_admin)


@app.route('/export', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def export():
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        ['ID', 'SUBJECT', 'THEME', 'DIFFICULTY', 'TITLE', 'STATEMENT', 'INPUT_FORMAT', 'OUTPUT_FORMAT', 'TIME_LIMIT',
         'MEMORY_LIMIT'])

    db_sess = db_session.create_session()
    tasks = db_sess.query(Tasks).all()

    for task in tasks:
        writer.writerow([
            task.id,
            task.subject,
            task.theme,
            task.difficulty,
            task.title,
            task.statement,
            task.input_format,
            task.output_format,
            task.time_limit,
            task.memory_limit
        ])

    output.seek(0)

    return Response(output, mimetype='text/csv', headers={'Content-Disposition': 'attachment;filename=task.csv'})


@app.route('/admin/task_delete', methods=["GET", "POST"])
@app.route('/admin/task_delete/<int:task_id>', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_task_delete(task_id=1):
    db_sess = db_session.create_session()
    task = db_sess.get(Tasks, task_id)
    db_sess.delete(task)
    db_sess.commit()
    return redirect('/admin')


@app.route('/admin/task_edit', methods=["GET", "POST"])
@app.route('/admin/task_edit/<int:task_id>', methods=["GET", "POST"])
@login_required
@admin_required
@user_ban
def admin_task_edit(task_id=1):
    subject = session['subject']
    db_sess = db_session.create_session()
    db_task = db_sess.query(Tasks).filter(Tasks.id == task_id).all()[0]
    db_subject = db_task.subject
    db_task_name = db_task.title
    db_memory_limit = db_task.memory_limit
    db_time_limit = db_task.time_limit
    db_task_description = db_task.statement
    db_input_data = db_task.input_format
    db_output_data = db_task.output_format
    db_level = db_task.difficulty
    db_theme = db_task.theme
    db_test = db_sess.query(TaskTest).filter(TaskTest.task_id == task_id).all()
    if db_task.subject == 'информатика':
        if request.method == "POST":
            task_name = request.form.get("task_name")
            memory_limit = request.form.get("memory_limit")
            time_limit = request.form.get("time_limit")
            task_description = request.form.get("task_description")
            input_data = request.form.get("input_data")
            output_data = request.form.get("output_data")
            level = request.form.get("level")
            theme = request.form.get("theme")
            test_list = []
            test_list.append((request.form.get("test1_input"), request.form.get("test1_output")))
            test_list.append((request.form.get("test2_input"), request.form.get("test2_output")))
            test_list.append((request.form.get("test3_input"), request.form.get("test3_output")))
            test_list.append((request.form.get("test4_input"), request.form.get("test4_output")))
            test_list.append((request.form.get("test5_input"), request.form.get("test5_output")))
            db_task.subject = db_subject
            db_task.title = task_name
            db_task.statement = task_description
            db_task.input_format = input_data
            db_task.output_format = output_data
            db_task.memory_limit = memory_limit
            db_task.time_limit = time_limit
            db_task.difficulty = level
            db_task.theme = theme
            for i in range(5):
                db_test[i].task_id = task_id
                db_test[i].input_data = test_list[i][0]
                db_test[i].output = test_list[i][1]
                db_sess.commit()
            return redirect('/admin')
    else:
        if request.method == "POST":
            db_sess = db_session.create_session()
            task_name = request.form.get("task_name")
            task_description = request.form.get("task_description")
            level = request.form.get("level")
            theme = request.form.get("theme")
            subject = db_subject
            db_task.title = task_name
            db_task.statement = task_description
            db_task.difficulty = level
            db_task.theme = theme
            db_test[0].task_id = task_id
            db_test[0].input_data = request.form.get("test_input")
            db_sess.commit()
            return redirect('/admin')
    return render_template("task_edit.html", subject=subject, db_task_name=db_task_name,
                           db_memory_limit=db_memory_limit, db_time_limit=db_time_limit,
                           db_task_description=db_task_description, db_input_data=db_input_data,
                           db_output_data=db_output_data, db_level=db_level, db_theme=db_theme,
                           db_subject=db_subject, db_test=db_test)


@app.route('/<subject>/pvp/create')
@login_required
@user_ban
def create_pvp(subject):
    ai_difficulty = "средняя"
    ai_task = generate_task(ai_difficulty, subject)
    while ai_task.get("error") is not None:
        ai_task = generate_task(ai_difficulty, subject)
    ai_theme = ai_task.get("тема")
    ai_task_name = ai_task.get("название задачи")
    ai_task_description = ai_task.get("условие задачи")
    ai_level = ai_difficulty
    ai_memory_limit = ai_task.get("лимит памяти")
    ai_time_limit = ai_task.get("лимит времени")
    ai_input_data = ai_task.get("входные данные")
    ai_output_data = ai_task.get("выходные данные")
    ai_test = []
    if subject == 'информатика':
        for i in range(1, 6):
            ai_test.append((ai_task.get(f"входные данные тест {i}"), ai_task.get(f"выходные данные тест {i}")))
    else:
        ai_test.append((ai_task.get("ответ"), ""))
    if subject == 'информатика':
        db_sess = db_session.create_session()
        task_name = ai_task_name
        memory_limit = ai_memory_limit
        time_limit = ai_time_limit
        task_description = ai_task_description
        input_data = ai_input_data
        output_data = ai_output_data
        theme = ai_theme
        test_list = []
        test_list.append((ai_test[0][0], ai_test[0][1]))
        test_list.append((ai_test[1][0], ai_test[1][1]))
        test_list.append((ai_test[2][0], ai_test[2][1]))
        test_list.append((ai_test[3][0], ai_test[3][1]))
        test_list.append((ai_test[4][0], ai_test[4][1]))
        task = Tasks(
            subject=subject,
            title=task_name,
            statement=task_description,
            input_format=input_data,
            output_format=output_data,
            memory_limit=memory_limit,
            time_limit=time_limit,
            difficulty=ai_level,
            theme=theme
        )
        task_id = db_sess.query(Tasks).all()[-1].id + 1
        for i in range(5):
            task_test = TaskTest(
                task_id=task_id,
                input_data=test_list[i][0],
                output=test_list[i][1],
            )
            db_sess.add(task_test)
        db_sess.add(task)
        db_sess.commit()
    else:
        db_sess = db_session.create_session()
        task_name = ai_task_name
        task_description = ai_task_description
        theme = ai_theme
        task = Tasks(
            subject=subject,
            title=task_name,
            statement=task_description,
            difficulty=ai_level,
            theme=theme
        )
        task_id = db_sess.query(Tasks).all()[-1].id + 1
        task_test = TaskTest(
            task_id=task_id,
            input_data=ai_test[0][0],
        )
        db_sess.add(task_test)
        db_sess.add(task)
        db_sess.commit()
    room = str(uuid.uuid4())
    session['room'] = room
    matches[room] = {
        'players': [current_user.id],
        'completed': {str(current_user.id): 0},
        'subject': subject,
        'task_id': task_id
    }
    return redirect(f'/{subject}/pvp/room/{room}')


@app.route('/<subject>/pvp/join/<room>')
@login_required
@user_ban
def join_pvp(subject, room):
    if room not in matches:
        abort(404)

    if matches[room].get('subject') != subject:
        abort(404)

    if len(matches[room]['players']) >= 2:
        return "комната заполнена", 400

    if current_user.id in matches[room]['players']:
        return redirect(f'/{subject}/pvp/room/{room}')

    matches[room]['players'].append(current_user.id)
    matches[room]['completed'][str(current_user.id)] = 0
    session['room'] = room
    return redirect(f'/{subject}/pvp/room/{room}')


@app.route('/<subject>/pvp', methods=["GET", "POST"])
@login_required
@user_ban
def pvp_choose(subject):
    open_rooms = []
    for room_id, info in matches.items():
        if info.get('subject') == subject and len(info['players']) < 2:
            open_rooms.append(room_id)
    return render_template('choose.html', rooms=open_rooms, subject=subject)


@app.route('/<subject>/task/<int:task_id>', methods=["GET", "POST"])
@login_required
@user_ban
def training(subject, task_id):
    db_sess = db_session.create_session()
    task = db_sess.get(Tasks, task_id)
    task_test = db_sess.query(TaskTest).filter(TaskTest.task_id == task.id).all()
    submission = db_sess.query(Submissions).filter(Submissions.task_id == task.id).all()
    submission_id = len(submission) + 1
    if subject == 'информатика':
        if request.method == "POST":
            file = request.files.get("file")
            if not file or file.filename == "":
                abort(400, "Файл не выбран")
            file.filename = f"submission_{submission_id}.py"
            os.makedirs(f"submissions_training/submissions_{current_user.id}/task_{task_id}", exist_ok=True)
            file.save(os.path.join(f"submissions_training/submissions_{current_user.id}/task_{task_id}", file.filename))

            # judge
            test_passed = 0
            f_err = 0
            for test in task_test:
                p = subprocess.Popen(
                    ["python3", f"submission_{submission_id}.py"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=f"submissions_training/submissions_{current_user.id}/task_{task_id}"
                )
                try:
                    out, err = p.communicate(test.input_data, timeout=task.time_limit)
                    out = out.strip()
                    if err:
                        submission_result = Submissions(
                            user_id=current_user.id,
                            task_id=task_id,
                            verdict=err.splitlines()[-1],
                            total_tests=test_passed,
                        )
                        f_err = 1
                    else:
                        if out == test.output.strip():
                            test_passed += 1
                except subprocess.TimeoutExpired:
                    print("Превышено максимальное время работы")
                    submission_result = Submissions(
                        user_id=current_user.id,
                        task_id=task_id,
                        verdict="Превышено максимальное время работы",
                        total_tests=test_passed,
                    )
                    f_err = 1
                    p.kill()
            if test_passed == 5:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="OK",
                    total_tests=test_passed,
                )
            elif f_err == 0:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="Частичное решение",
                    total_tests=test_passed,
                )
            db_sess.add(submission_result)
            db_sess.commit()
        last_submission = db_sess.query(Submissions).filter(Submissions.user_id == current_user.id,
                                                            Submissions.task_id == task_id).all()
        if last_submission:
            result = last_submission[-1]
            verdict = result.verdict
            test_passed = result.total_tests
        else:
            verdict = "Нет сданных решений"
            test_passed = None
        return render_template('training.html', task=task, test=task_test[0], verdict=verdict, test_passed=test_passed,
                               subject=subject)
    else:
        if request.method == "POST":
            answer = request.form.get("answer").lower()
            if task_test[0].input_data.lower() == answer:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="OK",
                )
            else:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="Неверный ответ, попробуйте снова",
                )
            db_sess.add(submission_result)
            db_sess.commit()
        last_submission = db_sess.query(Submissions).filter(Submissions.user_id == current_user.id,
                                                            Submissions.task_id == task_id).all()
        if last_submission:
            result = last_submission[-1]
            verdict = result.verdict
        else:
            verdict = "Нет сданных решений"
        return render_template('training_other.html', task=task, verdict=verdict, subject=subject)


@app.route('/<subject>/pvp/room/<room>', methods=["GET", "POST"])
@login_required
@user_ban
def pvp_room(subject, room):
    matches[room]['ochko'] = 0
    task_id = matches[room]['task_id']
    db_sess = db_session.create_session()
    task = db_sess.get(Tasks, task_id)
    task_test = db_sess.query(TaskTest).filter(TaskTest.task_id == task.id).all()
    submission = db_sess.query(Submissions).filter(Submissions.task_id == task.id).all()
    submission_id = len(submission) + 1
    if subject == 'информатика':
        if request.method == "POST":
            if len(matches[room]['players']) <= 1:
                message = "Ожидание второго игрока"
                return render_template('Pvp.html', room=room, task=task, test=task_test[0], players_info=[],
                                       verdict=message, test_passed=None, subject=subject)
            file = request.files.get("file")
            if not file or file.filename == "":
                abort(400, "Файл не выбран")
            file.filename = f"submission_{submission_id}.py"
            os.makedirs(f"submissions_pvp/submissions_{current_user.id}/task_{task_id}", exist_ok=True)
            file.save(os.path.join(f"submissions_pvp/submissions_{current_user.id}/task_{task_id}", file.filename))

            # judge
            test_passed = 0
            f_err = 0
            for test in task_test:
                p = subprocess.Popen(
                    ["python3", f"submission_{submission_id}.py"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=f"submissions_pvp/submissions_{current_user.id}/task_{task_id}"
                )
                try:
                    out, err = p.communicate(test.input_data, timeout=task.time_limit)
                    out = out.strip()
                    if err:
                        print(err)
                        submission_result = Submissions(
                            user_id=current_user.id,
                            task_id=task_id,
                            verdict=err.splitlines()[-1],
                            total_tests=test_passed,
                        )
                        f_err = 1
                    else:
                        if out == test.output.strip():
                            test_passed += 1
                except subprocess.TimeoutExpired:
                    print("Превышено максимальное время работы")
                    submission_result = Submissions(
                        user_id=current_user.id,
                        task_id=task_id,
                        verdict="Превышено максимальное время работы",
                        total_tests=test_passed,
                    )
                    f_err = 1
                    p.kill()
            if test_passed == 5:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="OK",
                    total_tests=test_passed,
                )
                matches[room]['ochko'] = 1
            elif f_err == 0:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="Частичное решение",
                    total_tests=test_passed,
                )
            db_sess.add(submission_result)
            db_sess.commit()

            uid = str(current_user.id)
            matches[room]['completed'][uid] = matches[room]['ochko']
            if matches[room]['ochko'] == 1:
                result = finish_match(room)
                socketio.emit('match_finished', {'result': result}, room=room)

                return redirect(f"/{subject}/pvp/results/{room}")

        players_info = []
        for uid_str in matches[room]['players']:
            user = db_sess.get(User, int(uid_str))
            players_info.append({'name': user.name, 'elo': user.elo_rating})

        last_submission = db_sess.query(Submissions).filter(Submissions.user_id == current_user.id,
                                                            Submissions.task_id == task_id).all()
        if last_submission:
            result = last_submission[-1]
            verdict = result.verdict
            test_passed = result.total_tests
        else:
            verdict = "Нет сданных решений"
            test_passed = None
        return render_template('Pvp.html', room=room, task=task, test=task_test[0], players_info=players_info,
                               verdict=verdict, test_passed=test_passed, subject=subject)
    else:
        if len(matches[room]['players']) <= 1:
            message = "Ожидание второго игрока"
            return render_template('pvp_other.html', room=room, task=task, test=task_test[0], players_info=[],
                                   verdict=message, subject=subject)
        if request.method == "POST":
            answer = request.form.get("answer").lower()
            if task_test[0].input_data.lower() == answer:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="OK",
                )
                matches[room]['ochko'] = 1
            else:
                submission_result = Submissions(
                    user_id=current_user.id,
                    task_id=task_id,
                    verdict="Неверный ответ, попробуйте снова",
                )
            db_sess.add(submission_result)
            db_sess.commit()

            uid = str(current_user.id)
            matches[room]['completed'][uid] = matches[room]['ochko']
            if matches[room]['ochko'] == 1:
                result = finish_match(room)
                socketio.emit('match_finished', {'result': result}, room=room)

                return redirect(f"/{subject}/pvp/results/{room}")

        players_info = []
        for uid_str in matches[room]['players']:
            user = db_sess.get(User, int(uid_str))
            players_info.append({'name': user.name, 'elo': user.elo_rating})

        last_submission = db_sess.query(Submissions).filter(Submissions.user_id == current_user.id,
                                                            Submissions.task_id == task_id).all()
        if last_submission:
            result = last_submission[-1]
            verdict = result.verdict
        else:
            verdict = "Нет сданных решений"
        return render_template('pvp_other.html', task=task, verdict=verdict, subject=subject, room=room,
                               test=task_test[0], players_info=players_info)


def finish_match(room):
    db_sess = db_session.create_session()
    players = matches[room]['players']
    completed = matches[room]['completed']

    user1_id, user2_id = players
    user1 = db_sess.get(User, user1_id)
    user2 = db_sess.get(User, user2_id)

    score1 = completed.get(str(user1_id), 0)
    score2 = completed.get(str(user2_id), 0)
    if score1 > score2:
        user1.elo_rating, user2.elo_rating = update_elo(user1.elo_rating, user2.elo_rating)
        result = f"{user1.name} победил"
    elif score2 > score1:
        user2.elo_rating, user1.elo_rating = update_elo(user2.elo_rating, user1.elo_rating)
        result = f"{user2.name} победил"
    else:
        user1.elo_rating, user2.elo_rating = update_elo(user1.elo_rating, user2.elo_rating, draw=True)
        result = "Ничья"

    db_sess.commit()
    matches[room]['finished'] = True
    matches[room]['result'] = result
    return result


@app.route('/<subject>/pvp/results/<room>')
@login_required
@user_ban
def pvp_results(subject, room):
    if room not in matches or not matches[room].get('finished'):
        return redirect(f"/{subject}/")

    db_sess = db_session.create_session()

    players_data = []
    result_text = matches[room]['result']
    completed = matches[room]['completed']

    winner_name = None
    if "победил" in result_text:
        winner_name = result_text.split()[0]

    for uid in matches[room]['players']:
        user = db_sess.get(User, uid)
        players_data.append({
            "name": user.name,
            "score": completed.get(str(uid), 0),
            "elo": user.elo_rating,
            "is_winner": user.name == winner_name
        })

    return render_template(
        "pvp_results.html",
        players=players_data,
        result=result_text,
        subject=subject
    )


@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    if room not in matches:
        return

    db_sess = db_session.create_session()
    scores = []
    for user_id_str, score in matches[room]['completed'].items():
        user = db_sess.get(User, int(user_id_str))
        scores.append({
            'name': user.name if user else "???",
            'score': score,
            'elo': user.elo_rating if user else 1000
        })
    emit('update_scores',
         {'scores': scores,
          'player_count': len(matches[room]['players'])},
         room=room)


@socketio.on('submit_code')
def on_submit(data):
    room = data['room']
    if room not in matches or current_user.id not in matches[room]['players']:
        return

    uid = str(current_user.id)
    matches[room]['completed'][uid] = max(matches[room]['completed'].get(uid, 0), data.get('test_passed', 0))

    db_sess = db_session.create_session()
    scores = []
    for user_id_str, score in matches[room]['completed'].items():
        user = db_sess.get(User, int(user_id_str))
        scores.append({
            'name': user.name if user else "???",
            'score': score,
            'elo': user.elo_rating if user else 1000
        })
    emit('update_scores', {'scores': scores, 'player_count': len(matches[room]['players'])}, room=room)
    if len(matches[room]['completed']) == 2 and not matches[room].get('finished'):
        result = finish_match(room)
        emit('match_finished', {'result': result}, room=room)


if __name__ == '__main__':
    socketio.run(app, port=8080, host='127.0.0.1', allow_unsafe_werkzeug=True, debug=True)
