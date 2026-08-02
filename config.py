# config.py - GPIO 映射 + 音符频率表

from machine import Pin, PWM

# --- 蜂鸣器 ---
BUZZER_PIN = 2

# --- 板载 LED ---
LED_GREEN = 32
LED_RED = 33

# --- 功能键 ---
KEY1_MODE = 34     # 模式切换：钢琴 <-> 自动演奏 <-> 录音
KEY2_OCTAVE = 35   # 切换八度

# --- 琴键 GPIO ---
KEY_PINS = {
    "C4": 5,
    "D4": 12,   # MTDI，启动需低电平
    "E4": 14,
    "F4": 19,
    "G4": 21,
    "A4": 22,
    "B4": 23,
}

# --- 音符频率 (Hz) ---
NOTE_FREQ = {
    "C4": 262, "D4": 294, "E4": 330, "F4": 349,
    "G4": 392, "A4": 440, "B4": 494,
    "C5": 523, "D5": 587, "E5": 659, "F5": 698,
    "G5": 784, "A5": 880, "B5": 988,
    "C3": 131, "D3": 147, "E3": 165, "F3": 175,
    "G3": 196, "A3": 220, "B3": 247,
}

DEFAULT_OCTAVE = {n: n for n in ["C4","D4","E4","F4","G4","A4","B4"]}
C3_OCTAVE = {
    "C4": "C3", "D4": "D3", "E4": "E3", "F4": "F3",
    "G4": "G3", "A4": "A3", "B4": "B3",
}

def init_buzzer():
    buzzer = PWM(Pin(BUZZER_PIN))
    buzzer.duty(0)
    return buzzer

def init_keys():
    keys = {}
    for name, gpio in KEY_PINS.items():
        keys[name] = Pin(gpio, Pin.IN, Pin.PULL_UP)
    key1 = Pin(KEY1_MODE, Pin.IN, Pin.PULL_UP)
    key2 = Pin(KEY2_OCTAVE, Pin.IN, Pin.PULL_UP)
    return keys, key1, key2
