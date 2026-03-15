import datetime
import sqlalchemy
from sqlalchemy import orm

from .db_session import SqlAlchemyBase


class Tasks(SqlAlchemyBase):
    __tablename__ = 'tasks'

    id = sqlalchemy.Column(sqlalchemy.Integer,
                           primary_key=True, autoincrement=True)
    subject = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    title = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    statement = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    input_format = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    output_format = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    memory_limit = sqlalchemy.Column(sqlalchemy.Integer, nullable=True)
    time_limit = sqlalchemy.Column(sqlalchemy.Integer, nullable=True)
    difficulty = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    created_at = sqlalchemy.Column(sqlalchemy.DateTime,
                                     default=datetime.datetime.now)
    task_tests = orm.relationship("TaskTest", back_populates='tasks')
    submissions = orm.relationship("Submissions", back_populates="tasks")
    theme = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
