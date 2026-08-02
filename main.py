# main.py - 电子钢琴 v3
#
# 操作：
#   KEY1 (GPIO34) = 录音
#   KEY2 (GPIO35) = 播放小星星
#   按任意琴键 = 退出播放/录音，回到钢琴
#
# 按键与LED串联 → LED硬件自动亮灭，无需软件控制

import time
from machine import Pin
from config import *

buzzer = init_buzzer()
keys, key1, key2 = init_keys()
green = Pin(LED_GREEN, Pin.OUT, value=1)
red   = Pin(LED_RED,   Pin.OUT, value=1)

_UP  = {k: k.replace("4","5") for k in DEFAULT_OCTAVE}
MAPS = {-1: C3_OCTAVE, 0: DEFAULT_OCTAVE, 1: _UP}

BPM = 100
BEAT = 60000 // BPM

SONG = ("小星星", [
    ("C4",1),("C4",1),("G4",1),("G4",1),("A4",1),("A4",1),("G4",2),
    ("F4",1),("F4",1),("E4",1),("E4",1),("D4",1),("D4",1),("C4",2),
    ("G4",1),("G4",1),("F4",1),("F4",1),("E4",1),("E4",1),("D4",2),
    ("G4",1),("G4",1),("F4",1),("F4",1),("E4",1),("E4",1),("D4",2),
    ("C4",1),("C4",1),("G4",1),("G4",1),("A4",1),("A4",1),("G4",2),
    ("F4",1),("F4",1),("E4",1),("E4",1),("D4",1),("D4",1),("C4",2),
])

octave = 0
cur_key = None
rec_data = []
freq_now = 0

_db = {}
DEBOUNCE = 30

def read_pin(pin, name):
    now = time.ticks_ms()
    val = pin.value()
    rec = _db.get(name)
    if rec is None:
        _db[name] = [val, val, now]
        return val, False
    sv, rv, rt = rec
    if val != rv:
        _db[name][1] = val
        _db[name][2] = now
        return sv, False
    if val != sv and time.ticks_diff(now, rt) >= DEBOUNCE:
        just = (sv == 1 and val == 0)
        _db[name][0] = val
        _db[name][1] = val
        _db[name][2] = now
        return val, just
    return sv, False

def any_key():
    for n, p in keys.items():
        if p.value() == 0:
            return True
    return False

def sound(note):
    global freq_now
    f = NOTE_FREQ.get(note, 0)
    if f and f != freq_now:
        buzzer.freq(f)
        buzzer.duty(384)
        freq_now = f

def mute():
    global freq_now
    if freq_now:
        buzzer.duty(0)
        freq_now = 0

def beep(note, ms):
    f = NOTE_FREQ.get(note, 0)
    if f:
        buzzer.freq(f)
        buzzer.duty(384)
    time.sleep_ms(max(ms - 10, 1))
    buzzer.duty(0)
    time.sleep_ms(10)

def flash(led, n=1, d=60):
    for _ in range(n):
        led.value(0)
        time.sleep_ms(d)
        led.value(1)
        time.sleep_ms(d)

def piano():
    global cur_key, octave
    mp = MAPS.get(octave, DEFAULT_OCTAVE)
    hit = None
    for n, p in keys.items():
        v, _ = read_pin(p, n)
        if v == 0:
            hit = n
            break
    if hit:
        if cur_key != hit:
            cur_key = hit
            sound(mp[hit])
    else:
        if cur_key is not None:
            cur_key = None
            mute()

def auto_play():
    name, melody = SONG
    flash(green, 1, 60)
    print("Playing:", name)
    for note, beats in melody:
        if any_key():
            mute()
            time.sleep_ms(300)
            flash(green, 2, 60)
            print("Stopped, back to Piano")
            return
        dur = int(BEAT * beats)
        beep(note, dur)
    mute()
    time.sleep_ms(400)

def do_record():
    global rec_data, cur_key, octave
    rec_data = []
    flash(red, 3, 100)
    print("Recording...")
    last_k = None
    t0 = time.ticks_ms()
    mp = MAPS.get(octave, DEFAULT_OCTAVE)
    while True:
        now = time.ticks_ms()
        # KEY2 在录音期间切换八度
        _, j2 = read_pin(key2, "key2")
        if j2:
            octave = -1 if octave >= 1 else octave + 1
            mp = MAPS.get(octave, DEFAULT_OCTAVE)
            flash(red if octave < 0 else green, 1, 30)
        # KEY1 停止录音
        _, j1 = read_pin(key1, "key1")
        if j1:
            if last_k:
                dur = time.ticks_diff(now, t0)
                rec_data.append((mp.get(last_k, last_k), dur))
            mute()
            break

        hit = None
        for n, p in keys.items():
            v, _ = read_pin(p, n)
            if v == 0:
                hit = n
                break
        if hit:
            if last_k != hit:
                if last_k:
                    dur = time.ticks_diff(now, t0)
                    rec_data.append((mp.get(last_k, last_k), dur))
                last_k = hit
                t0 = now
                sound(mp[hit])
                flash(green, 1, 15)
        else:
            if last_k is not None:
                dur = time.ticks_diff(now, t0)
                rec_data.append((mp.get(last_k, last_k), dur))
                last_k = None
                mute()
        time.sleep_ms(5)
    mute()
    cnt = len(rec_data)
    print(f"Recorded: {cnt} notes")
    if cnt > 0:
        flash(green, 2, 120)
        time.sleep_ms(300)
        print("Playback...")
        for note, dur in rec_data:
            beep(note, max(dur, 20))
            # 完整回放，不打断
    mute()
    _db.clear()
    flash(green, 2, 60)
    print("Mode: Piano")

print("=== Digital Piano v3 ===")
print("KEY1=Record | KEY2=Play | AnyKey=Stop")
flash(green, 2, 80)

_c1 = 0  # KEY1 防连跳
_c2 = 0  # KEY2 防连跳

while True:
    now = time.ticks_ms()

    # KEY2 = 播放小星星
    _, j2 = read_pin(key2, "key2")
    if j2 and time.ticks_diff(now, _c2) > 0:
        _c2 = now + 400
        auto_play()
        time.sleep_ms(100)

    # KEY1 = 录音
    _, j1 = read_pin(key1, "key1")
    if j1 and time.ticks_diff(now, _c1) > 0:
        _c1 = now + 400
        do_record()
        time.sleep_ms(100)

    piano()
    time.sleep_ms(5)
