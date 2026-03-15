import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models, callbacks
import json
import os
from collections import Counter


DATA_PATH = 'data.npz'
MODEL_NAME = 'alien_signals_model.h5'
ANALYTICS_NAME = 'analytics.json'
CLASSES_MAP_NAME = 'classes_map.json'
FFT_SIZE = 64
TARGET_FRAMES = 64


def get_spectrogram(x_data):
    spectrograms = []
    for sig in x_data:
        s = np.array(sig).flatten()
        needed_len = TARGET_FRAMES * FFT_SIZE
        if len(s) < needed_len:
            s = np.pad(s, (0, needed_len - len(s)))
        else:
            s = s[:needed_len]


        spec = np.abs(np.fft.rfft(s.reshape(TARGET_FRAMES, FFT_SIZE), axis=1))
        spec = np.log(spec + 1e-7)


        spec = (spec - np.mean(spec)) / (np.std(spec) + 1e-7)
        spectrograms.append(spec)
    return np.array(spectrograms)


def robust_clean(x_data, y_data, label_map=None):
    clean_x, clean_y = [], []
    if label_map is None: label_map = {}
    for i in range(len(y_data)):
        try:
            lbl_str = str(y_data[i]).strip()
            planet_name = lbl_str[32:] if len(lbl_str) > 32 else lbl_str
            if not planet_name: continue
            if planet_name not in label_map:
                label_map[planet_name] = len(label_map)
            val = label_map[planet_name]
            sig = x_data[i]
            if len(sig) > 0:
                clean_x.append(sig)
                clean_y.append(val)
        except:
            continue
    return clean_x, np.array(clean_y), label_map


def main():
    loader = np.load(DATA_PATH, allow_pickle=True)
    tx_raw, ty, label_map = robust_clean(loader['train_x'], loader['train_y'])
    vx_raw, vy, _ = robust_clean(loader['valid_x'], loader['valid_y'], label_map)

    with open(CLASSES_MAP_NAME, 'w', encoding='utf-8') as f:
        json.dump(label_map, f, ensure_ascii=False, indent=4)

    print("🎨 Генерация продвинутых спектрограмм...")
    tx = get_spectrogram(tx_raw)
    vx = get_spectrogram(vx_raw)
    tx = np.expand_dims(tx, axis=-1)
    vx = np.expand_dims(vx, axis=-1)

    num_classes = len(label_map)

    model = models.Sequential([
        layers.Input(shape=tx.shape[1:]),


        layers.Conv2D(64, (3, 3), activation='relu', padding='same'),
        layers.BatchNormalization(),
        layers.Conv2D(64, (3, 3), activation='relu', padding='same'),
        layers.MaxPooling2D((2, 2)),


        layers.Conv2D(128, (3, 3), activation='relu', padding='same'),
        layers.BatchNormalization(),
        layers.MaxPooling2D((2, 2)),


        layers.GlobalMaxPooling2D(),

        layers.Dense(256, activation='relu'),
        layers.Dropout(0.4),
        layers.Dense(128, activation='relu'),
        layers.Dense(num_classes, activation='softmax')
    ])

    lr_reducer = callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.5, patience=5, min_lr=1e-5)

    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])


    history = model.fit(
        tx, ty,
        validation_data=(vx, vy),
        epochs=60,  # Чуть больше эпох для сложной модели
        batch_size=32,
        callbacks=[lr_reducer]
    )

    model.save(MODEL_NAME)


    inv_map = {v: k for k, v in label_map.items()}
    unique, counts = np.unique(ty, return_counts=True)
    train_dist = {inv_map[int(k)]: int(v) for k, v in zip(unique, counts)}
    top_5_val = [[inv_map[int(k)], int(v)] for k, v in Counter(vy).most_common(5)]

    analytics = {
        "history": {
            "epochs": list(range(1, len(history.history['accuracy']) + 1)),
            "accuracy": [round(float(x), 4) for x in history.history['accuracy']],
            "val_accuracy": [round(float(x), 4) for x in history.history['val_accuracy']],
            "loss": [round(float(x), 4) for x in history.history['loss']]
        },
        "train_distribution": train_dist,
        "top_5_validation": top_5_val
    }
    with open(ANALYTICS_NAME, 'w', encoding='utf-8') as f:
        json.dump(analytics, f, ensure_ascii=False, indent=4)



if __name__ == "__main__":
    main()