import sqlalchemy
from sqlalchemy import orm

from .db_session import SqlAlchemyBase


class TaskTest(SqlAlchemyBase):
    __tablename__ = 'task_tests'
    id = sqlalchemy.Column(sqlalchemy.Integer,
                           primary_key=True, autoincrement=True)
    task_id = sqlalchemy.Column(sqlalchemy.Integer,
                                sqlalchemy.ForeignKey("tasks.id"))
    input_data = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    output = sqlalchemy.Column(sqlalchemy.Text, nullable=True)
    tasks = orm.relationship("Tasks", back_populates='task_tests')
