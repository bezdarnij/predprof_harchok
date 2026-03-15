import numpy as np
from sklearn.preprocessing import LabelEncoder

# Загрузка
data = np.load('train_valid.npz') # Укажи правильное имя файла
train_x = data['train_x']
train_y = data['train_y']
valid_x = data['valid_x']
valid_y = data['valid_y']

# Восстановление классов (начинаем с 0 по ТЗ)
le = LabelEncoder()
train_y_encoded = le.fit_transform(train_y)
valid_y_encoded = le.transform(valid_y)

# Если train_x — это аудиосигналы (одномерные массивы),
# убедись, что они имеют одинаковую длину или извлеки признаки (MFCC), как мы обсуждали.

import sqlite3
import hashlib


def get_hash(password, salt="alien_salt_2226"):
    return hashlib.md5((password + salt).encode()).hexdigest()


def init_db():
    conn = sqlite3.connect('app.db')
    cur = conn.cursor()
    # Таблица пользователей
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
                   (id INTEGER PRIMARY KEY, login TEXT, password TEXT, 
                    role TEXT, first_name TEXT, last_name TEXT)''')

    # Добавим админа по умолчанию, если его нет
    cur.execute("SELECT * FROM users WHERE login='admin'")
    if not cur.fetchone():
        admin_pass = get_hash("admin123")
        cur.execute("INSERT INTO users (login, password, role, first_name, last_name) VALUES (?,?,?,?,?)",
                    ('admin', admin_pass, 'admin', 'Михаил', 'Ученый'))
    conn.commit()
    conn.close()

import tensorflow as tf
from tensorflow.keras import layers, models

model = models.Sequential([
    layers.Input(shape=(train_x.shape[1], train_x.shape[2] if train_x.ndim > 2 else 1)),
    layers.Conv1D(64, 3, activation='relu'),
    layers.MaxPooling1D(2),
    layers.Flatten(),
    layers.Dense(128, activation='relu'),
    layers.Dropout(0.3),
    layers.Dense(len(le.classes_), activation='softmax')
])

model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

# Сохраняем историю для графиков аналитики
history = model.fit(train_x, train_y_encoded, epochs=20,
                    validation_data=(valid_x, valid_y_encoded))

model.save('model.h5')

import matplotlib.pyplot as plt

# 1. Точность от эпох
def plot_accuracy(history):
    plt.plot(history.history['accuracy'], label='Train')
    plt.plot(history.history['val_accuracy'], label='Valid')
    plt.title('Accuracy vs Epochs')
    plt.legend()
    plt.savefig('static/acc_plot.png')

# 2. Распределение классов (диаграмма)
def plot_distribution(train_y):
    unique, counts = np.unique(train_y, return_counts=True)
    plt.bar(unique, counts)
    plt.title('Signals per Civilization')
    plt.savefig('static/dist_plot.png')

import unittest
class TestAuth(unittest.TestCase):
    def test_hash(self):
        self.assertEqual(get_hash("123"), get_hash("123"))