import numpy as np
import tensorflow as tf
from tensorflow.keras import layers, models, callbacks
import json
import os
from collections import Counter
from sklearn.utils import class_weight

DATA_PATH = 'data.npz'
MODEL_NAME = 'alien_signals_model.h5'
ANALYTICS_NAME = 'analytics.json'
CLASSES_MAP_NAME = 'classes_map.json'
FFT_SIZE = 128
TARGET_FRAMES = 128


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

        spec = np.log(spec + 1e-9)

        spec = (spec - np.min(spec)) / (np.max(spec) - np.min(spec) + 1e-9)
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
    if not os.path.exists(DATA_PATH): return

    loader = np.load(DATA_PATH, allow_pickle=True)
    tx_raw, ty, label_map = robust_clean(loader['train_x'], loader['train_y'])
    vx_raw, vy, _ = robust_clean(loader['valid_x'], loader['valid_y'], label_map)

    with open(CLASSES_MAP_NAME, 'w', encoding='utf-8') as f:
        json.dump(label_map, f, ensure_ascii=False, indent=4)

    tx = get_spectrogram(tx_raw)
    vx = get_spectrogram(vx_raw)
    tx = np.expand_dims(tx, axis=-1)
    vx = np.expand_dims(vx, axis=-1)

    num_classes = len(label_map)


    weights = class_weight.compute_class_weight('balanced', classes=np.unique(ty), y=ty)
    class_weights = dict(enumerate(weights))


    model = models.Sequential([
        layers.Input(shape=tx.shape[1:]),

        layers.Conv2D(32, (3, 3), padding='same', activation='relu'),
        layers.BatchNormalization(),
        layers.Conv2D(32, (3, 3), padding='same', activation='relu'),
        layers.MaxPooling2D((2, 2)),

        layers.Conv2D(64, (3, 3), padding='same', activation='relu'),
        layers.BatchNormalization(),
        layers.Conv2D(64, (3, 3), padding='same', activation='relu'),
        layers.MaxPooling2D((2, 2)),

        layers.Conv2D(128, (3, 3), padding='same', activation='relu'),
        layers.BatchNormalization(),
        layers.GlobalAveragePooling2D(),

        layers.Dense(256, activation='relu'),
        layers.Dropout(0.5),
        layers.Dense(num_classes, activation='softmax')
    ])


    opt = tf.keras.optimizers.Adam(learning_rate=0.001)
    model.compile(optimizer=opt, loss='sparse_categorical_crossentropy', metrics=['accuracy'])


    lr_sch = callbacks.ReduceLROnPlateau(monitor='accuracy', factor=0.5, patience=3, min_lr=1e-6)


    history = model.fit(
        tx, ty,
        validation_data=(vx, vy),
        epochs=100,
        batch_size=16,
        class_weight=class_weights,
        callbacks=[lr_sch],
        verbose=1
    )

    model.save(MODEL_NAME)


    inv_map = {v: k for k, v in label_map.items()}
    analytics = {
        "history": {
            "epochs": list(range(1, len(history.history['accuracy']) + 1)),
            "accuracy": [round(float(x), 4) for x in history.history['accuracy']],
            "val_accuracy": [round(float(x), 4) for x in history.history['val_accuracy']],
            "loss": [round(float(x), 4) for x in history.history['loss']]
        },
        "train_distribution": {inv_map[int(k)]: int(v) for k, v in Counter(ty).items()},
        "top_5_validation": [[inv_map[int(k)], int(v)] for k, v in Counter(vy).most_common(5)]
    }
    with open(ANALYTICS_NAME, 'w', encoding='utf-8') as f:
        json.dump(analytics, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main()