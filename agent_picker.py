"""
AGENT PICKER (A-P) - автовыбор агента по картинке иконки + клики через AutoHotkey.
Не лезет в процесс игры: только скриншоты экрана и клики.
AutoHotkey запускается один раз вместе с программой и ждёт команд (WM_COPYDATA).

Запуск из исходников:  python agent_picker.py      (лучше от администратора)
Сборка в .exe:         build.bat

Все данные (конфиг, иконки агентов, AHK-скрипты, рантайм AutoHotkey) лежат в
%APPDATA%\\AgentPicker  - папка создаётся при первом запуске.
"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:  # до создания окна Tk, чтобы координаты мыши и скриншота совпадали
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass
try:  # отдельная иконка в панели задач
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AgentPicker.App")
except Exception:
    pass

import cv2
import keyboard
import mss

MSS = getattr(mss, "MSS", None) or mss.mss   # новое имя в свежих версиях mss
import numpy as np
import pyautogui

# ---------- пути ----------
APP_NAME = "AgentPicker"
FROZEN = getattr(sys, "frozen", False)
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", SRC_DIR)                      # ресурсы, вшитые в exe
EXE_DIR = os.path.dirname(sys.executable) if FROZEN else SRC_DIR  # где лежит программа
DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
AGENTS_DIR = os.path.join(DATA_DIR, "agents")
AHK_DIR = os.path.join(DATA_DIR, "ahk")
AHK_BIN_DIR = os.path.join(AHK_DIR, "runtime")
AHK_EXE = os.path.join(AHK_BIN_DIR, "AutoHotkey64.exe")
AHK_TEMPLATE_PATH = os.path.join(DATA_DIR, "ahk_template.ahk")
CFG_PATH = os.path.join(DATA_DIR, "config.json")
POS_PATH = os.path.join(AGENTS_DIR, "positions.json")
SCROLLS_PATH = os.path.join(AGENTS_DIR, "scrolls.json")   # сколько щелчков колеса вниз до каждого агента
CRASH_LOG = os.path.join(DATA_DIR, "crash.log")

KNOWN_THRESH = 0.90    # "эта иконка уже изучена"
DETECT_THRESH = 0.85   # "мы на экране выбора"
POLL = 0.25            # период опроса экрана, сек
PICK_DELAY = 0.15      # пауза перед поиском агента после появления экрана выбора
DETECT_SCALE = 0.5     # во сколько раз уменьшать картинку для детекта экрана
DETECT_THRESH_SMALL = 0.80
MAX_SCROLL_STEPS = 5   # максимум шагов прокрутки списка при запасном поиске агента
MAX_LEARN_PAGES = 12   # максимум страниц списка при обучении
SCROLL_SETTLE = 0.30   # пауза после прокрутки, пока список доедет (анимация), сек
TOP_RESET_NOTCHES = 30 # столько щелчков вверх гарантированно возвращает список в начало
END_DIFF = 3.0         # если после прокрутки картинка сетки почти не изменилась - список закончился

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

DEFAULTS = {
    "priority": [],
    "lockin": None,
    "thresh": 0.80,
    "scroll_clicks": 3,
    "hk_auto": "f9",
    "hk_pick": "f11",
    "hk_lockin": "f7",
    "notify_sound": True,    # звуковой сигнал при вкл/выкл авто-режима
    "notify_popup": True,    # плашка поверх игры при вкл/выкл авто-режима
}

AHK_TEMPLATE_VERSION = "AP-SERVER-V2"   # по этой метке понимаем, что шаблон уже в новом формате
AHK_WINDOW_TITLE = "AgentPickerAHK"     # заголовок скрытого окна AHK-сервера (должен совпадать с шаблоном)

# AHK запускается один раз и ждёт команд от программы (WM_COPYDATA), поэтому на выбор агента
# не тратится время на старт процесса. Править можно только блок "ВЫБОР АГЕНТА".
DEFAULT_AHK_TEMPLATE = """#Requires AutoHotkey v2.0
#SingleInstance Force
#NoTrayIcon
Persistent
CoordMode "Mouse", "Screen"

; AP-SERVER-V2
; AHK запускается вместе с Agent Picker и ждёт команд. Править можно только блок
; "ВЫБОР АГЕНТА". После правки нажми "ПЕРЕЗАПУСТИТЬ AHK" в настройках программы.

; ===================== ВЫБОР АГЕНТА =====================
Repeats := 4   ; сколько раз повторить пару "агент + выбрать"

PickAgent(Name, AgentX, AgentY, LockX, LockY) {
    Loop Repeats {
        MouseClick "left", AgentX, AgentY, 1, 0, "D"
        Sleep 25
        MouseClick "left", AgentX, AgentY, 1, 0, "U"

        Sleep 30

        MouseClick "left", LockX, LockY, 1, 0, "D"
        Sleep 25
        MouseClick "left", LockX, LockY, 1, 0, "U"

        Sleep 60
    }
}

; ===================== СЛУЖЕБНАЯ ЧАСТЬ (не менять) =====================
Busy := false
Srv := Gui(, "AgentPickerAHK")
OnMessage(0x4A, OnCopyData)   ; WM_COPYDATA

OnCopyData(wParam, lParam, msg, hwnd) {
    cmd := StrGet(NumGet(lParam, 2 * A_PtrSize, "Ptr"), "UTF-16")
    p := StrSplit(cmd, "|")
    try {
        switch p[1] {
            case "PICK":     ; PICK|имя|ax|ay|lx|ly - выполняется в фоне, ответ сразу
                SetTimer(RunPick.Bind(p[2], Integer(p[3]), Integer(p[4]), Integer(p[5]), Integer(p[6])), -1)
            case "SCROLL":   ; SCROLL|cx|cy|nx|ny|вверх|вниз|щелчков_вверх - ответ после прокрутки
                DoScroll(Integer(p[2]), Integer(p[3]), Integer(p[4]), Integer(p[5]), Integer(p[6]), Integer(p[7]), Integer(p[8]))
            case "EXIT":
                SetTimer(() => ExitApp(), -1)
        }
    }
    return 1
}

RunPick(Name, AX, AY, LX, LY) {
    global Busy
    if Busy
        return
    Busy := true
    try PickAgent(Name, AX, AY, LX, LY)
    Busy := false
}

DoScroll(cx, cy, nx, ny, toTop, down, topNotches) {
    MouseMove cx, cy, 0
    Sleep 40
    if toTop {
        MouseClick "WheelUp", cx, cy, topNotches, 0
        Sleep 80
    }
    Loop down {
        MouseClick "WheelDown", cx, cy, 1, 0
        Sleep 20
    }
    Sleep 40
    MouseMove nx, ny, 0
}
"""


def ensure_template():
    """Создаёт шаблон AHK; старый формат (скрипт на каждого агента) сохраняет в .old и заменяет."""
    msgs = []
    if os.path.exists(AHK_TEMPLATE_PATH):
        with open(AHK_TEMPLATE_PATH, "r", encoding="utf-8-sig") as f:
            text = f.read()
        if AHK_TEMPLATE_VERSION in text:
            return msgs
        shutil.copy2(AHK_TEMPLATE_PATH, os.path.join(DATA_DIR, "ahk_template.old.ahk"))
        msgs.append("Шаблон AHK обновлён до нового формата (старый сохранён как ahk_template.old.ahk).")
    with open(AHK_TEMPLATE_PATH, "w", encoding="utf-8-sig") as f:
        f.write(DEFAULT_AHK_TEMPLATE)
    return msgs


# ---------- первый запуск: папка в AppData ----------
def init_data():
    """Создаёт %APPDATA%\\AgentPicker со всем содержимым. Возвращает список сообщений для журнала."""
    msgs = []
    first_run = not os.path.exists(CFG_PATH)
    for d in (DATA_DIR, AGENTS_DIR, AHK_DIR, AHK_BIN_DIR):
        os.makedirs(d, exist_ok=True)

    # перенос данных из старой версии (папки рядом с программой)
    if os.path.normcase(EXE_DIR) != os.path.normcase(DATA_DIR):
        old_agents = os.path.join(EXE_DIR, "agents")
        has_png = any(f.lower().endswith(".png") for f in os.listdir(AGENTS_DIR))
        if os.path.isdir(old_agents) and not has_png:
            n = 0
            for f in os.listdir(old_agents):
                if f.lower().endswith(".png") or f.lower() == "positions.json":
                    shutil.copy2(os.path.join(old_agents, f), os.path.join(AGENTS_DIR, f))
                    n += 1
            if n:
                msgs.append(f"Перенесено файлов из старой папки agents: {n}.")
        for old, new in (("picker_gui_config.json", CFG_PATH), ("ahk_template.ahk", AHK_TEMPLATE_PATH)):
            src = os.path.join(EXE_DIR, old)
            if os.path.exists(src) and not os.path.exists(new):
                shutil.copy2(src, new)
                msgs.append(f"Перенесён файл: {old}")

    # рантайм AutoHotkey, вшитый в exe
    if not os.path.exists(AHK_EXE):
        bundled = os.path.join(RES_DIR, "AutoHotkey64.exe")
        if os.path.exists(bundled):
            shutil.copy2(bundled, AHK_EXE)
            msgs.append("AutoHotkey runtime установлен в папку данных.")

    msgs.extend(ensure_template())
    if first_run:
        msgs.insert(0, f"Первый запуск: создана папка данных {DATA_DIR}")
    return msgs


def find_ahk_exe():
    cands = [AHK_EXE]
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "AutoHotkey", "v2", "AutoHotkey64.exe"))
            cands.append(os.path.join(base, "Programs", "AutoHotkey", "v2", "AutoHotkey64.exe"))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def load_positions():
    try:
        with open(POS_PATH, "r", encoding="utf-8") as f:
            return {k: tuple(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def save_positions(pos):
    with open(POS_PATH, "w", encoding="utf-8") as f:
        json.dump({k: list(v) for k, v in pos.items()}, f, ensure_ascii=False, indent=2)


_u32 = ctypes.WinDLL("user32", use_last_error=True)   # отдельный экземпляр, чтобы не трогать argtypes общего windll
_u32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_u32.FindWindowW.restype = wintypes.HWND
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindow.restype = wintypes.BOOL
_u32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                     wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
_u32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
_u32.GetParent.argtypes = [wintypes.HWND]
_u32.GetParent.restype = wintypes.HWND
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
_u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_u32.SetWindowLongW.restype = ctypes.c_long

WM_COPYDATA = 0x004A
SMTO_ABORTIFHUNG = 0x0002


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p)]


class AhkServer:
    """Постоянно работающий AHK-процесс. Команды уходят ему через WM_COPYDATA - без запуска процесса на каждый клик."""

    def __init__(self):
        self.proc = None
        self.lock = threading.RLock()
        self.last_error = ""

    @staticmethod
    def _find():
        return _u32.FindWindowW(None, AHK_WINDOW_TITLE) or 0

    def alive(self):
        h = self._find()
        return bool(h) and bool(_u32.IsWindow(h))

    def start(self, wait=5.0):
        """Запускает AHK-сервер, если он ещё не работает. Возвращает True, когда он готов принимать команды."""
        with self.lock:
            if self.alive():
                return True
            exe = find_ahk_exe()
            try:
                if exe:
                    self.proc = subprocess.Popen([exe, AHK_TEMPLATE_PATH], cwd=AHK_DIR, creationflags=0x08000000)
                else:
                    os.startfile(AHK_TEMPLATE_PATH)   # запасной вариант: установленный в системе AutoHotkey v2
            except Exception as e:
                self.last_error = str(e)
                return False
            deadline = time.time() + wait
            while time.time() < deadline:
                if self.alive():
                    return True
                if self.proc is not None and self.proc.poll() is not None:
                    self.last_error = f"AHK завершился сразу после запуска (код {self.proc.returncode}) - проверь шаблон."
                    return False
                time.sleep(0.02)
            self.last_error = "AHK не создал окно для команд за отведённое время."
            return False

    def send(self, text, timeout=15000, autostart=True):
        """Отправляет команду. Для SCROLL возвращается после окончания прокрутки, для PICK - сразу."""
        with self.lock:
            if autostart and not self.start():
                raise RuntimeError("AutoHotkey не запущен: " + self.last_error)
            hwnd = self._find()
            if not hwnd:
                raise RuntimeError("окно AHK-сервера не найдено")
            buf = ctypes.create_unicode_buffer(text)
            cds = COPYDATASTRUCT(1, ctypes.sizeof(buf), ctypes.cast(buf, ctypes.c_void_p))
            res = ctypes.c_size_t(0)
            ok = _u32.SendMessageTimeoutW(hwnd, WM_COPYDATA, 0, ctypes.addressof(cds),
                                          SMTO_ABORTIFHUNG, timeout, ctypes.byref(res))
            if not ok:
                raise RuntimeError("AHK не ответил на команду")
            return res.value

    def stop(self):
        with self.lock:
            if self.alive():
                try:
                    self.send("EXIT", timeout=1000, autostart=False)
                except Exception:
                    pass
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=1.0)
                except Exception:
                    try:
                        self.proc.terminate()
                    except Exception:
                        pass
                self.proc = None


def load_scrolls():
    try:
        with open(SCROLLS_PATH, "r", encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def save_scrolls(s):
    with open(SCROLLS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# ---------- утилиты ----------
def load_cfg():
    cfg = dict(DEFAULTS)
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except Exception:
            pass
    return cfg


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def imread_u(path):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_u(path, img):
    ok, buf = cv2.imencode(".png", img)
    if ok:
        buf.tofile(path)


def to_photo(img, scale=1):
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode(".png", img)
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes()))


def to_photo_size(img, size):
    h, w = img.shape[:2]
    k = size / float(max(h, w))
    interp = cv2.INTER_AREA if k < 1 else cv2.INTER_NEAREST
    return to_photo(cv2.resize(img, None, fx=k, fy=k, interpolation=interp))


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True


class Geo:
    """Геометрия сетки агентов (эталон 1920x1080, масштабируется по высоте экрана)."""

    def __init__(self):
        with MSS() as sct:
            self.mon = dict(sct.monitors[1])
        self.W, self.H = self.mon["width"], self.mon["height"]
        s = self.s = self.H / 1080.0
        self.cell = int(round(88 * s))
        self.mg = max(2, int(round(8 * s)))
        self.size = self.cell - 2 * self.mg
        self.px, self.py = 99.3 * s, 99.4 * s
        self.x0, self.y0 = 41 * s, 308 * s
        self.rx1, self.ry1 = int(36 * s), int(300 * s)
        self.rx2, self.ry2 = int(436 * s), int(898 * s)
        self.list_center = (self.mon["left"] + (self.rx1 + self.rx2) // 2,
                            self.mon["top"] + (self.ry1 + self.ry2) // 2)      # куда наводить для прокрутки
        self.neutral = (self.mon["left"] + int(self.W * 0.6), self.mon["top"] + int(self.H * 0.5))  # мышь вне сетки
        self.lockin_default = (
            self.mon["left"] + self.W // 2 - int(5 * s),
            self.mon["top"] + int(763 * s),
        )


def grab(g):
    with MSS() as sct:
        img = np.array(sct.grab(g.mon))
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def grid_region(g, img):
    return img[g.ry1:g.ry2, g.rx1:g.rx2]


def best_match(region, tpl):
    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return 0.0, (0, 0)
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def same_region(a, b):
    """True, если два снимка сетки почти одинаковые (список не сдвинулся = дошли до конца)."""
    if a.shape != b.shape:
        return False
    return float(cv2.absdiff(a, b).mean()) < END_DIFF


def load_templates():
    t = {}
    for f in sorted(os.listdir(AGENTS_DIR)):
        if f.lower().endswith(".png"):
            img = imread_u(os.path.join(AGENTS_DIR, f))
            if img is not None:
                t[os.path.splitext(f)[0]] = img
    return t


def cell_origins(g, img, templates):
    region = grid_region(g, img)
    bx, by = g.x0, g.y0
    for tpl in templates.values():
        score, loc = best_match(region, tpl)
        if score >= KNOWN_THRESH:
            bx = g.rx1 + loc[0] - g.mg
            by = g.ry1 + loc[1] - g.mg
            break
    xs = [bx + k * g.px for k in range(-6, 7)]
    ys = [by + k * g.py for k in range(-12, 13)]
    xs = [x for x in xs if x >= g.rx1 - 3 and x + g.cell <= g.rx2 + 3]
    ys = [y for y in ys if y >= g.ry1 - 3 and y + g.cell <= g.ry2 + 3]
    return xs, ys


# ---------- стиль VALORANT ----------
SCALE = 1.0


def px(n):
    return int(round(n * SCALE))


C = dict(
    bg="#0F1923", bar="#0B121A", panel="#16212C", panel2="#1D2A38", line="#34424F",
    red="#FF4655", red_h="#FF6573", teal="#17E0C0", cream="#ECE8E1", muted="#7C8794", dark="#0F1923",
)
F = {}


def init_fonts(root):
    fams = set(tkfont.families(root))

    def pick(*cands):
        for c in cands:
            if c in fams:
                return c
        return "Segoe UI"

    head = pick("Bahnschrift SemiBold Condensed", "Bahnschrift Condensed", "Bahnschrift", "Oswald", "Segoe UI Semibold")
    body = pick("Bahnschrift SemiLight", "Bahnschrift", "Segoe UI")
    hb = "normal" if "SemiBold" in head else "bold"
    F["h1"] = (head, 17, hb)
    F["h2"] = (head, 13, hb)
    F["btn"] = (head, 12, hb)
    F["body"] = (body, 10)
    F["body_b"] = (body, 10, "bold")
    F["small"] = (body, 8)
    F["mono"] = ("Consolas", 9)


def init_style(root):
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    st.layout("V.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
        "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    st.configure("V.Vertical.TScrollbar", background=C["line"], troughcolor=C["panel"], bordercolor=C["panel"],
                 lightcolor=C["line"], darkcolor=C["line"], width=px(8))
    st.map("V.Vertical.TScrollbar", background=[("active", C["muted"])])


def dark_titlebar(win):
    """Тёмная рамка окна (Windows 10/11)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        one = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(one), 4)
        col = ctypes.c_int(0x0023190F)   # #0F1923 в формате BGR
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), 4)
    except Exception:
        pass


class VButton(tk.Canvas):
    """Кнопка в стиле VALORANT: двойная рамка, красная/кремовая/контурная."""
    STYLES = {
        "primary": dict(fill=C["red"], hover=C["red_h"], fg="#FFFFFF", border="#FFFFFF", gap=3),
        "light": dict(fill=C["cream"], hover="#FFFFFF", fg=C["dark"], border=C["cream"], gap=3),
        "ghost": dict(fill=C["bg"], hover=C["panel2"], fg=C["cream"], border=C["line"], gap=0),
    }

    def __init__(self, parent, text, command, kind="ghost", font=None, padx=18, height=36, minw=0):
        self.font = tkfont.Font(font=font or F["btn"])
        self.text, self.kind, self.command = text, kind, command
        self.bgc = parent["bg"]
        self.w = max(self.font.measure(text) + 2 * px(padx), px(minw))
        self.h = px(height)
        super().__init__(parent, width=self.w, height=self.h, bg=self.bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._release)
        self.draw()

    def _set_hover(self, v):
        self.hover = v
        self.draw()

    def _release(self, e):
        if 0 <= e.x <= self.w and 0 <= e.y <= self.h and self.command:
            self.command()

    def set_text(self, text):
        self.text = text
        self.draw()

    def set_kind(self, kind):
        self.kind = kind
        self.draw()

    def draw(self):
        self.delete("all")
        s = self.STYLES[self.kind]
        fill = s["hover"] if self.hover else s["fill"]
        w, h, gap = self.w, self.h, px(s["gap"])
        if gap:
            self.create_rectangle(0, 0, w - 1, h - 1, outline=s["border"], fill=self.bgc)
            self.create_rectangle(gap, gap, w - 1 - gap, h - 1 - gap, outline=fill, fill=fill)
        else:
            self.create_rectangle(0, 0, w - 1, h - 1, outline=s["border"], fill=fill)
        self.create_text(w // 2, h // 2, text=self.text, fill=s["fg"], font=self.font)


class TabBar(tk.Canvas):
    """Верхняя панель как в клиенте: логотип, вкладки со скошенным активным блоком, статус справа."""

    def __init__(self, parent, tabs, on_change):
        super().__init__(parent, height=px(50), bg=C["bar"], highlightthickness=0, bd=0)
        self.tabs, self.on_change = tabs, on_change
        self.active = tabs[0][0]
        self.regions = []
        self.status = ("АВТО ВЫКЛ", C["red"])
        self.font = tkfont.Font(font=F["h2"])
        self.logo_font = tkfont.Font(font=F["h1"])
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.click)
        self.bind("<Motion>", self.motion)

    def set_active(self, key):
        self.active = key
        self.draw()

    def set_status(self, text, color):
        self.status = (text, color)
        self.draw()

    def click(self, e):
        for x1, x2, key in self.regions:
            if x1 <= e.x <= x2:
                self.on_change(key)
                return

    def motion(self, e):
        over = any(x1 <= e.x <= x2 for x1, x2, _ in self.regions)
        self.config(cursor="hand2" if over else "")

    def draw(self):
        self.delete("all")
        h, w = int(self["height"]), max(self.winfo_width(), 100)
        cy = h // 2
        d = px(11)
        self.create_polygon(px(18), cy - d, px(18) + d, cy, px(18), cy + d, px(18) - d, cy, fill=C["red"], outline="")
        self.create_text(px(40), cy, text="AGENT PICKER", anchor="w", fill=C["cream"], font=self.logo_font)
        x = px(40) + self.logo_font.measure("AGENT PICKER") + px(34)
        self.regions = []
        for key, label in self.tabs:
            tw = self.font.measure(label) + px(40)
            if key == self.active:
                self.create_polygon(x, 0, x + tw, 0, x + tw - px(14), h, x, h, fill=C["cream"], outline="")
                self.create_text(x + (tw - px(7)) / 2, cy, text=label, fill=C["dark"], font=self.font)
            else:
                self.create_text(x + tw / 2, cy, text=label, fill=C["muted"], font=self.font)
            self.regions.append((x, x + tw, key))
            x += tw + px(6)
        txt, col = self.status
        self.create_text(w - px(16), cy, text=txt, anchor="e", fill=col, font=self.font)
        tx = w - px(16) - self.font.measure(txt) - px(14)
        self.create_oval(tx - px(4), cy - px(4), tx + px(4), cy + px(4), fill=col, outline="")


def panel(parent, **kw):
    return tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"], **kw)


def panel_header(parent, text, bg=None):
    bg = bg or C["panel"]
    f = tk.Frame(parent, bg=bg)
    tk.Label(f, text=text, font=F["h2"], fg=C["cream"], bg=bg).pack(anchor="w")
    tk.Frame(f, height=1, bg=C["line"]).pack(fill="x", pady=(3, 0))
    tk.Frame(f, height=2, width=px(36), bg=C["red"]).place(x=0, rely=1.0, y=-2)
    f.pack(fill="x")
    return f


def make_entry(parent, var, width=14, readonly=False):
    return tk.Entry(parent, textvariable=var, width=width, font=F["body"], bg=C["bg"], fg=C["cream"],
                    readonlybackground=C["bg"], insertbackground=C["cream"], relief="flat",
                    highlightthickness=1, highlightbackground=C["line"], highlightcolor=C["teal"],
                    state="readonly" if readonly else "normal")


def make_spin(parent, var, lo, hi, step, width=7, command=None):
    return tk.Spinbox(parent, from_=lo, to=hi, increment=step, textvariable=var, width=width, font=F["body"],
                      bg=C["bg"], fg=C["cream"], insertbackground=C["cream"], relief="flat",
                      buttonbackground=C["panel2"], highlightthickness=1, highlightbackground=C["line"],
                      highlightcolor=C["teal"], command=command)


class IconGrid(tk.Frame):
    """Прокручиваемая сетка иконок агентов."""

    def __init__(self, parent, cols, on_select, on_double):
        super().__init__(parent, bg=C["panel"])
        self.cols, self.on_select, self.on_double = cols, on_select, on_double
        self.tile_w, self.tile_h = px(80), px(92)
        width = cols * (self.tile_w + px(8))
        self.canvas = tk.Canvas(self, bg=C["panel"], highlightthickness=0, width=width, height=px(330))
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="V.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y", padx=(4, 0))
        self.inner = tk.Frame(self.canvas, bg=C["panel"])
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.tiles, self.photos, self.selected = {}, [], None

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def set_items(self, names, templates, selected=None):
        for w in self.inner.winfo_children():
            w.destroy()
        self.tiles, self.photos, self.selected = {}, [], selected
        for i, n in enumerate(names):
            tile = tk.Frame(self.inner, bg=C["line"], width=self.tile_w, height=self.tile_h, cursor="hand2")
            tile.pack_propagate(False)
            tile.grid(row=i // self.cols, column=i % self.cols, padx=4, pady=4)
            ph = to_photo_size(templates[n], px(64))
            self.photos.append(ph)
            inner = tk.Frame(tile, bg=C["panel2"])
            inner.pack(fill="both", expand=True, padx=1, pady=1)
            img = tk.Label(inner, image=ph, bg=C["panel2"], bd=0)
            img.pack(pady=(px(6), 0))
            nm = tk.Label(inner, text=n, font=F["small"], fg=C["cream"], bg=C["panel2"])
            nm.pack(pady=(2, 0))
            for wdg in (tile, inner, img, nm):
                wdg.bind("<Button-1>", lambda e, n=n: self.on_select(n))
                wdg.bind("<Double-Button-1>", lambda e, n=n: self.on_double(n))
            self.tiles[n] = tile
        self.highlight(selected)

    def highlight(self, name):
        self.selected = name
        for n, t in self.tiles.items():
            t.config(bg=C["red"] if n == name else C["line"])


class VDialog(tk.Toplevel):
    """Тёмное диалоговое окно вместо системного messagebox."""

    def __init__(self, app, title, text, buttons):
        super().__init__(app.root)
        self.configure(bg=C["bg"])
        self.title(title)
        self.transient(app.root)
        self.resizable(False, False)
        self.value = buttons[-1][2]
        box = panel(self)
        box.pack(padx=px(14), pady=px(14))
        inner = tk.Frame(box, bg=C["panel"], padx=px(22), pady=px(18))
        inner.pack()
        tk.Label(inner, text=title.upper(), font=F["h2"], fg=C["cream"], bg=C["panel"]).pack(anchor="w")
        tk.Frame(inner, height=2, width=px(36), bg=C["red"]).pack(anchor="w", pady=(3, 10))
        tk.Label(inner, text=text, font=F["body"], fg=C["cream"], bg=C["panel"], justify="left",
                 wraplength=px(420)).pack(anchor="w")
        row = tk.Frame(inner, bg=C["panel"])
        row.pack(anchor="e", pady=(16, 0))
        for label, kind, val in buttons:
            VButton(row, label, lambda v=val: self.done(v), kind).pack(side="left", padx=4)
        self.protocol("WM_DELETE_WINDOW", lambda: self.done(buttons[-1][2]))
        dark_titlebar(self)
        app.center(self)
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass

    def done(self, v):
        self.value = v
        self.destroy()


class NameDialog(tk.Toplevel):
    """Запрос имени для новой иконки при обучении."""

    def __init__(self, app, cell, ri, ci, page=1):
        super().__init__(app.root)
        self.configure(bg=C["bg"])
        self.title("Новая иконка")
        self.transient(app.root)
        self.resizable(False, False)
        self.result = ("skip", None)
        box = panel(self)
        box.pack(padx=px(14), pady=px(14))
        inner = tk.Frame(box, bg=C["panel"], padx=px(24), pady=px(18))
        inner.pack()
        tk.Label(inner, text="НОВАЯ ИКОНКА", font=F["h2"], fg=C["cream"], bg=C["panel"]).pack(anchor="w")
        tk.Frame(inner, height=2, width=px(36), bg=C["red"]).pack(anchor="w", pady=(3, 10))
        self.photo = to_photo(cell, 3)
        frame = tk.Frame(inner, bg=C["red"], padx=2, pady=2)
        frame.pack()
        tk.Label(frame, image=self.photo, bd=0).pack()
        tk.Label(inner, text=f"Страница {page}, ряд {ri + 1}, колонка {ci + 1}. Как зовут агента?", font=F["body"],
                 fg=C["cream"], bg=C["panel"]).pack(pady=(10, 4))
        self.var = tk.StringVar()
        ent = make_entry(inner, self.var, width=26)
        ent.pack(ipady=4)
        ent.focus_set()
        row = tk.Frame(inner, bg=C["panel"])
        row.pack(pady=(14, 0))
        VButton(row, "СОХРАНИТЬ", self.save, "primary").pack(side="left", padx=4)
        VButton(row, "ПРОПУСТИТЬ", self.skip, "ghost").pack(side="left", padx=4)
        VButton(row, "ЗАКОНЧИТЬ", self.stop, "ghost").pack(side="left", padx=4)
        ent.bind("<Return>", lambda e: self.save())
        self.protocol("WM_DELETE_WINDOW", self.stop)
        dark_titlebar(self)
        app.center(self)
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass

    def save(self):
        name = re.sub(r'[\\/:*?"<>|]', "", self.var.get()).strip()
        self.result = ("save", name) if name else ("skip", None)
        self.destroy()

    def skip(self):
        self.result = ("skip", None)
        self.destroy()

    def stop(self):
        self.result = ("stop", None)
        self.destroy()


class Toast(tk.Toplevel):
    """Плашка-уведомление поверх всех окон: не забирает фокус у игры, клики проходят сквозь неё."""

    def __init__(self, app, text, sub, color, ms=1800):
        super().__init__(app.root)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        self.configure(bg=color)
        inner = tk.Frame(self, bg=C["bar"], padx=px(22), pady=px(12))
        inner.pack(padx=(px(6), 0))
        tk.Label(inner, text=text, font=F["h1"], fg=C["cream"], bg=C["bar"]).pack()
        if sub:
            tk.Label(inner, text=sub, font=F["small"], fg=C["muted"], bg=C["bar"]).pack(pady=(2, 0))
        self.update_idletasks()
        try:   # NOACTIVATE | TOOLWINDOW | TRANSPARENT (клики насквозь), ставим до показа окна
            hwnd = _u32.GetParent(self.winfo_id()) or self.winfo_id()
            ex = _u32.GetWindowLongW(hwnd, -20)
            _u32.SetWindowLongW(hwnd, -20, ex | 0x08000000 | 0x00000080 | 0x00000020)
        except Exception:
            pass
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        self.geometry(f"+{x}+{px(70)}")
        self.deiconify()
        self.after(ms, self.close)

    def close(self):
        try:
            self.destroy()
        except tk.TclError:
            pass


# ---------- приложение ----------
class App:
    def __init__(self, root, init_msgs):
        self.root = root
        self.q = queue.Queue()
        self.cfg = load_cfg()
        self.templates = {}
        self.gray, self.gray_small, self.hit_order = [], [], []
        self.auto = False
        self.armed = True
        self.pick_lock = threading.Lock()
        self.hk_handles = {}
        self.pri_photos = []
        self.selected = None
        self.positions = load_positions()
        self.scrolls = load_scrolls()   # имя агента -> щелчков колеса вниз от начала списка
        self.learn = None
        self.ahk = AhkServer()
        self.toast = None

        self.build_ui()
        self.hwnd = 0
        try:
            root.update_idletasks()
            self.hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        except Exception:
            pass
        self.reload_templates()
        self.bind_hotkeys()
        self.update_lockin_label()
        self.poll_queue()
        threading.Thread(target=self.watcher, daemon=True).start()
        threading.Thread(target=self.ahk_boot, daemon=True).start()   # AHK стартует сразу и ждёт команд
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        for m in init_msgs:
            self.log(m)
        self.log("Готово. Если агенты ещё не изучены - зайди на экран выбора агента и нажми 'ОБУЧИТЬ'.")
        if not is_admin():
            self.log("ВНИМАНИЕ: запущено не от администратора - хоткеи и клики в игре могут не работать.")
        if not find_ahk_exe():
            self.log("ВНИМАНИЕ: AutoHotkey v2 не найден (ни вшитый, ни установленный) - клики работать не будут.")

    # ----- окна и диалоги -----
    def center(self, win):
        win.update_idletasks()
        r = self.root
        x = r.winfo_rootx() + (r.winfo_width() - win.winfo_width()) // 2
        y = r.winfo_rooty() + (r.winfo_height() - win.winfo_height()) // 3
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def info(self, title, text):
        d = VDialog(self, title, text, [("ОК", "primary", True)])
        self.root.wait_window(d)

    def ask(self, title, text, yes="ДА", no="НЕТ"):
        d = VDialog(self, title, text, [(yes, "primary", True), (no, "ghost", False)])
        self.root.wait_window(d)
        return d.value

    # ----- UI -----
    def build_ui(self):
        r = self.root
        r.title("Agent Picker")
        r.configure(bg=C["bg"])
        r.geometry(f"{px(980)}x{px(720)}")
        r.minsize(px(920), px(660))
        for name in ("icon.ico",):
            p = os.path.join(RES_DIR, name)
            if os.path.exists(p):
                try:
                    r.iconbitmap(p)
                except tk.TclError:
                    pass
        dark_titlebar(r)

        self.tabbar = TabBar(r, [("main", "ГЛАВНАЯ"), ("settings", "НАСТРОЙКИ"), ("log", "ЖУРНАЛ")], self.show_page)
        self.tabbar.pack(fill="x")
        self.footer = tk.Label(r, text="", anchor="w", font=F["small"], fg=C["muted"], bg=C["bar"],
                               padx=px(14), pady=px(5))
        self.footer.pack(fill="x", side="bottom")
        body = tk.Frame(r, bg=C["bg"])
        body.pack(fill="both", expand=True)
        self.body = body
        self.pages = {k: tk.Frame(body, bg=C["bg"]) for k in ("main", "settings", "log")}
        self.build_main(self.pages["main"])
        self.build_settings(self.pages["settings"])
        self.build_log(self.pages["log"])
        self.show_page("main")

    def show_page(self, key):
        for p in self.pages.values():
            p.pack_forget()
        self.pages[key].pack(fill="both", expand=True, padx=px(18), pady=px(14))
        self.tabbar.set_active(key)

    def build_main(self, p):
        p.columnconfigure(1, weight=1)
        p.rowconfigure(0, weight=1)

        left = panel(p)
        left.grid(row=0, column=0, sticky="ns", padx=(0, px(12)))
        li = tk.Frame(left, bg=C["panel"], padx=px(14), pady=px(12))
        li.pack(fill="both", expand=True)
        panel_header(li, "ИЗУЧЕННЫЕ АГЕНТЫ")
        self.grid_w = IconGrid(li, 4, self.on_select, lambda n: (self.on_select(n), self.add_priority()))
        self.grid_w.pack(fill="both", expand=True, pady=px(10))
        bl = tk.Frame(li, bg=C["panel"])
        bl.pack(fill="x")
        VButton(bl, "В ПРИОРИТЕТ", self.add_priority, "light").pack(side="left")
        VButton(bl, "УДАЛИТЬ", self.delete_agent, "ghost").pack(side="left", padx=6)

        right = panel(p)
        right.grid(row=0, column=1, sticky="nsew")
        ri = tk.Frame(right, bg=C["panel"], padx=px(14), pady=px(12))
        ri.pack(fill="both", expand=True)
        panel_header(ri, "ПРИОРИТЕТ ВЫБОРА")
        tk.Label(ri, text="Сверху - главный. Если он занят, берётся следующий.", font=F["small"],
                 fg=C["muted"], bg=C["panel"]).pack(anchor="w", pady=(6, 6))
        self.pri_inner = tk.Frame(ri, bg=C["panel"])
        self.pri_inner.pack(fill="both", expand=True)

        ctl = tk.Frame(p, bg=C["bg"])
        ctl.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(px(14), 0))
        btns = tk.Frame(ctl, bg=C["bg"])
        btns.pack(fill="x")
        self.btn_auto = VButton(btns, "ЗАПУСТИТЬ АВТО", self.toggle_auto, "primary", height=46, minw=250)
        self.btn_auto.pack(side="left")
        VButton(btns, "ОБУЧИТЬ", self.start_learn, "light", height=46).pack(side="left", padx=(10, 0))
        VButton(btns, "ВЫБРАТЬ СЕЙЧАС", self.pick_now_button, "ghost", height=46).pack(side="left", padx=(10, 0))
        VButton(btns, "ЗАПОМНИТЬ КНОПКУ", self.lockin_button, "ghost", height=46).pack(side="left", padx=(10, 0))
        self.lockin_lbl = tk.Label(ctl, text="", font=F["small"], fg=C["muted"], bg=C["bg"])
        self.lockin_lbl.pack(anchor="w", pady=(8, 0))

    def build_settings(self, p):
        box = panel(p)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=C["panel"], padx=px(16), pady=px(12))
        inner.pack(fill="x")
        panel_header(inner, "ПОИСК И ПРОКРУТКА")
        self.v_thresh = tk.StringVar(value=str(self.cfg["thresh"]))
        self.v_scroll = tk.StringVar(value=str(self.cfg["scroll_clicks"]))
        grid = tk.Frame(inner, bg=C["panel"])
        grid.pack(fill="x", pady=(10, 4))
        tk.Label(grid, text="Порог совпадения иконки", font=F["body"], fg=C["cream"], bg=C["panel"]).grid(row=0, column=0, sticky="w", pady=4)
        make_spin(grid, self.v_thresh, 0.5, 0.99, 0.01, command=self.apply_settings).grid(row=0, column=1, padx=16, ipady=3)
        tk.Label(grid, text="Щелчков колеса за шаг прокрутки", font=F["body"], fg=C["cream"], bg=C["panel"]).grid(row=1, column=0, sticky="w", pady=4)
        make_spin(grid, self.v_scroll, 1, 20, 1, command=self.apply_settings).grid(row=1, column=1, padx=16, ipady=3)
        for v in (self.v_thresh, self.v_scroll):
            v.trace_add("write", lambda *a: self.apply_settings())

        box2 = panel(p)
        box2.pack(fill="x", pady=(px(12), 0))
        in2 = tk.Frame(box2, bg=C["panel"], padx=px(16), pady=px(12))
        in2.pack(fill="x")
        panel_header(in2, "ГОРЯЧИЕ КЛАВИШИ")
        self.hk_vars = {}
        grid2 = tk.Frame(in2, bg=C["panel"])
        grid2.pack(fill="x", pady=(10, 4))
        rows = [("hk_auto", "Авто вкл/выкл"), ("hk_pick", "Выбрать сейчас"), ("hk_lockin", "Запомнить кнопку (курсор на ней)")]
        for i, (key, label) in enumerate(rows):
            tk.Label(grid2, text=label, font=F["body"], fg=C["cream"], bg=C["panel"]).grid(row=i, column=0, sticky="w", pady=5)
            var = tk.StringVar(value=self.cfg[key])
            self.hk_vars[key] = var
            make_entry(grid2, var, width=14, readonly=True).grid(row=i, column=1, padx=16, ipady=3)
            VButton(grid2, "ЗАДАТЬ", lambda k=key: self.capture_hotkey(k), "ghost", height=30).grid(row=i, column=2)

        box4 = panel(p)
        box4.pack(fill="x", pady=(px(12), 0))
        in4 = tk.Frame(box4, bg=C["panel"], padx=px(16), pady=px(12))
        in4.pack(fill="x")
        panel_header(in4, "УВЕДОМЛЕНИЯ ОБ АВТО-РЕЖИМЕ")
        self.v_nsound = tk.BooleanVar(value=bool(self.cfg.get("notify_sound", True)))
        self.v_npopup = tk.BooleanVar(value=bool(self.cfg.get("notify_popup", True)))
        for var, label in ((self.v_nsound, "Звуковой сигнал (два тона вверх - вкл, вниз - выкл)"),
                           (self.v_npopup, "Плашка поверх игры (работает в режиме «без рамки»)")):
            tk.Checkbutton(in4, text=label, variable=var, command=self.apply_notify, font=F["body"], fg=C["cream"],
                           bg=C["panel"], selectcolor=C["bg"], activebackground=C["panel"],
                           activeforeground=C["cream"], highlightthickness=0, bd=0, anchor="w").pack(fill="x", pady=(8, 0))

        box3 = panel(p)
        box3.pack(fill="x", pady=(px(12), 0))
        in3 = tk.Frame(box3, bg=C["panel"], padx=px(16), pady=px(12))
        in3.pack(fill="x")
        panel_header(in3, "ДАННЫЕ")
        tk.Label(in3, text=DATA_DIR, font=F["small"], fg=C["muted"], bg=C["panel"]).pack(anchor="w", pady=(8, 8))
        row = tk.Frame(in3, bg=C["panel"])
        row.pack(anchor="w")
        VButton(row, "ОТКРЫТЬ ПАПКУ ДАННЫХ", lambda: os.startfile(DATA_DIR), "ghost").pack(side="left")
        VButton(row, "ШАБЛОН AHK", self.open_template, "ghost").pack(side="left", padx=8)
        VButton(row, "ПЕРЕЗАПУСТИТЬ AHK", self.restart_ahk, "ghost").pack(side="left")

    def build_log(self, p):
        box = panel(p)
        box.pack(fill="both", expand=True)
        inner = tk.Frame(box, bg=C["panel"], padx=px(12), pady=px(10))
        inner.pack(fill="both", expand=True)
        panel_header(inner, "ЖУРНАЛ")
        wrap = tk.Frame(inner, bg=C["panel"])
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.txt = tk.Text(wrap, state="disabled", wrap="word", font=F["mono"], bg=C["bg"], fg=C["cream"],
                           relief="flat", highlightthickness=1, highlightbackground=C["line"], padx=8, pady=6)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.txt.yview, style="V.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(4, 0))
        self.txt.pack(side="left", fill="both", expand=True)

    # ----- служебное -----
    def post(self, fn, *args):
        self.q.put((fn, args))

    def poll_queue(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception as e:
                    self._log(f"Ошибка: {e}")
        except queue.Empty:
            pass
        self.root.after(100, self.poll_queue)

    def _log(self, msg):
        self.txt.config(state="normal")
        self.txt.insert("end", time.strftime("[%H:%M:%S] ") + msg + "\n")
        self.txt.see("end")
        self.txt.config(state="disabled")
        self.footer.config(text=msg)

    def log(self, msg):
        self.post(self._log, msg)

    def apply_settings(self):
        try:
            self.cfg["thresh"] = max(0.5, min(0.99, float(self.v_thresh.get().replace(",", "."))))
            self.cfg["scroll_clicks"] = max(1, min(20, int(float(self.v_scroll.get()))))
            save_cfg(self.cfg)
        except ValueError:
            pass

    def apply_notify(self):
        self.cfg["notify_sound"] = bool(self.v_nsound.get())
        self.cfg["notify_popup"] = bool(self.v_npopup.get())
        save_cfg(self.cfg)

    def ahk_boot(self):
        t0 = time.perf_counter()
        if self.ahk.start():
            self.log(f"AutoHotkey запущен и ждёт команд ({int((time.perf_counter() - t0) * 1000)} мс).")
        else:
            self.log("ВНИМАНИЕ: не удалось запустить AHK-сервер: " + self.ahk.last_error)

    def ahk_ensure(self):
        if not self.ahk.alive():
            self.ahk_boot()

    def restart_ahk(self):
        def work():
            self.ahk.stop()
            time.sleep(0.2)
            self.ahk_boot()
        threading.Thread(target=work, daemon=True).start()

    def open_template(self):
        try:
            subprocess.Popen(["notepad.exe", AHK_TEMPLATE_PATH])
        except Exception as e:
            self._log(f"Не удалось открыть шаблон: {e}")

    def on_close(self):
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        save_cfg(self.cfg)
        try:
            self.ahk.stop()
        except Exception:
            pass
        self.root.destroy()

    # ----- агенты и приоритет -----
    def reload_templates(self):
        self.templates = load_templates()
        self.gray = [cv2.cvtColor(t, cv2.COLOR_BGR2GRAY) for t in self.templates.values()]
        self.gray_small = [cv2.resize(t, None, fx=DETECT_SCALE, fy=DETECT_SCALE, interpolation=cv2.INTER_AREA)
                           for t in self.gray]
        self.hit_order = []   # индексы иконок, которые срабатывали раньше - проверяем их первыми
        self.cfg["priority"] = [n for n in self.cfg["priority"] if n in self.templates]
        self.scrolls = {k: v for k, v in self.scrolls.items() if k in self.templates}
        if self.selected not in self.templates:
            self.selected = None
        self.grid_w.set_items(list(self.templates), self.templates, self.selected)
        self.refresh_priority()
        save_cfg(self.cfg)

    def on_select(self, name):
        self.selected = name
        self.grid_w.highlight(name)

    def refresh_priority(self):
        for w in self.pri_inner.winfo_children():
            w.destroy()
        self.pri_photos = []
        pri = self.cfg["priority"]
        if not pri:
            tk.Label(self.pri_inner, text="Пока пусто. Выбери агента слева и нажми «В ПРИОРИТЕТ».",
                     font=F["body"], fg=C["muted"], bg=C["panel"]).pack(anchor="w", pady=10)
            return
        for i, n in enumerate(pri):
            row = tk.Frame(self.pri_inner, bg=C["panel2"])
            row.pack(fill="x", pady=3)
            tk.Frame(row, width=px(4), bg=C["red"] if i == 0 else C["line"]).pack(side="left", fill="y")
            tk.Label(row, text=str(i + 1), font=F["h1"], fg=C["red"] if i == 0 else C["muted"], bg=C["panel2"],
                     width=2).pack(side="left", padx=(10, 4))
            ph = to_photo_size(self.templates[n], px(40))
            self.pri_photos.append(ph)
            tk.Label(row, image=ph, bg=C["panel2"], bd=0).pack(side="left", pady=5)
            tk.Label(row, text=n.upper(), font=F["h2"], fg=C["cream"], bg=C["panel2"]).pack(side="left", padx=12)
            VButton(row, "✕", lambda i=i: self.remove_priority(i), "ghost", padx=9, height=28, font=F["body_b"]).pack(side="right", padx=(2, 8))
            VButton(row, "▼", lambda i=i: self.move_priority(i, 1), "ghost", padx=9, height=28, font=F["body_b"]).pack(side="right", padx=2)
            VButton(row, "▲", lambda i=i: self.move_priority(i, -1), "ghost", padx=9, height=28, font=F["body_b"]).pack(side="right", padx=2)

    def add_priority(self):
        n = self.selected
        if n and n in self.templates and n not in self.cfg["priority"]:
            self.cfg["priority"].append(n)
            self.refresh_priority()
            save_cfg(self.cfg)

    def remove_priority(self, i):
        if 0 <= i < len(self.cfg["priority"]):
            del self.cfg["priority"][i]
            self.refresh_priority()
            save_cfg(self.cfg)

    def move_priority(self, i, d):
        p = self.cfg["priority"]
        j = i + d
        if 0 <= i < len(p) and 0 <= j < len(p):
            p[i], p[j] = p[j], p[i]
            self.refresh_priority()
            save_cfg(self.cfg)

    def delete_agent(self):
        n = self.selected
        if not n:
            return
        if self.ask("Удалить агента", f"Удалить шаблон «{n}»?", "УДАЛИТЬ", "ОТМЕНА"):
            try:
                os.remove(os.path.join(AGENTS_DIR, n + ".png"))
            except OSError:
                pass
            self.positions.pop(n, None)
            self.scrolls.pop(n, None)
            save_scrolls(self.scrolls)
            self.selected = None
            self.reload_templates()

    # ----- обучение -----
    def start_learn(self):
        if self.auto:
            self.toggle_auto()
        self.info("Обучение",
                  "После нажатия OK окно свернётся. За 5 секунд переключись на Valorant "
                  "(экран выбора агента, список в самом начале) и убери мышь с сетки.\n\n"
                  "Дальше программа сама прокрутит список колесом, снимет все страницы и запомнит, "
                  "сколько щелчков вниз до каждого агента. Мышь не трогай. "
                  "На каждой странице будет короткий сигнал.")
        self.learn = {"notches": 0, "page": 0, "prev": None, "added": 0, "updated": 0}
        self.root.iconify()
        self.root.after(5000, self.learn_step)

    def match_known(self, cell):
        for n, t in self.templates.items():
            if best_match(cell, t)[0] >= KNOWN_THRESH:
                return n
        return None

    def learn_step(self):
        st = self.learn
        if st is None:
            return
        g = Geo()
        clicks = max(1, int(self.cfg["scroll_clicks"]))
        try:
            if st["page"] == 0:
                pyautogui.moveTo(*g.neutral)
                time.sleep(0.4)
            else:
                self.scroll_list(g, down=clicks)
                st["notches"] += clicks
            img = grab(g)
        except Exception as e:
            self.learn_finish(f"Ошибка: {e}.")
            return
        region = grid_region(g, img)
        if st["prev"] is not None and same_region(region, st["prev"]):
            self.learn_finish("Дошли до конца списка.")
            return
        st["prev"] = region.copy()
        st["page"] += 1
        try:
            import winsound
            winsound.Beep(900, 120)
        except Exception:
            pass

        xs, ys = cell_origins(g, img, self.templates)
        shown, stop = False, False
        for ri, y in enumerate(ys):
            for ci, x in enumerate(xs):
                if stop:
                    break
                yy, xx = int(round(y)) + g.mg, int(round(x)) + g.mg
                cell = img[yy:yy + g.size, xx:xx + g.size]
                if cell.shape[:2] != (g.size, g.size) or cell.std() < 12:
                    continue
                known = self.match_known(cell)
                if known:
                    old = self.scrolls.get(known)
                    if old is None or st["notches"] < old:   # запоминаем самую раннюю страницу, где агент виден
                        self.scrolls[known] = st["notches"]
                        st["updated"] += 1
                    continue
                if not shown:
                    self.root.deiconify()
                    self.root.lift()
                    shown = True
                dlg = NameDialog(self, cell, ri, ci, st["page"])
                self.root.wait_window(dlg)
                action, name = dlg.result
                if action == "stop":
                    stop = True
                elif action == "save":
                    imwrite_u(os.path.join(AGENTS_DIR, name + ".png"), cell)
                    self.templates[name] = cell
                    self.scrolls[name] = st["notches"]
                    st["added"] += 1
                    try:
                        cx = g.mon["left"] + xx + g.size // 2
                        cy = g.mon["top"] + yy + g.size // 2
                        self.positions[name] = (cx, cy)
                        save_positions(self.positions)
                    except Exception as e:
                        self._log(f"Не удалось сохранить позицию {name}: {e}")
        save_scrolls(self.scrolls)
        self._log(f"Страница {st['page']} (прокрутка {st['notches']}): добавлено {st['added']}, "
                  f"обновлено прокруток {st['updated']}.")
        if stop:
            self.learn_finish("Обучение остановлено.")
            return
        if st["page"] >= MAX_LEARN_PAGES:
            self.learn_finish("Достигнут предел страниц.")
            return
        if shown:
            self.root.iconify()          # вернуть фокус игре перед следующей прокруткой
        self.root.after(1500 if shown else 150, self.learn_step)

    def learn_finish(self, msg):
        st = self.learn or {"added": 0}
        self.learn = None
        self.root.deiconify()
        self.root.lift()
        self.reload_templates()
        save_scrolls(self.scrolls)
        self._log(f"{msg} Добавлено агентов: {st['added']}, всего изучено: {len(self.templates)}.")

    # ----- хоткеи -----
    def bind_hotkeys(self):
        self.unbind_hotkeys()
        actions = {
            "hk_auto": lambda: self.post(self.toggle_auto),
            "hk_pick": lambda: threading.Thread(target=self.do_pick, daemon=True).start(),
            "hk_lockin": lambda: self.post(self.set_lockin, pyautogui.position()),
        }
        for key, fn in actions.items():
            combo = (self.cfg.get(key) or "").strip()
            if combo:
                try:
                    self.hk_handles[key] = keyboard.add_hotkey(combo, fn)
                except Exception as e:
                    self.log(f"Не удалось повесить хоткей '{combo}': {e}")

    def unbind_hotkeys(self):
        for h in self.hk_handles.values():
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self.hk_handles = {}

    def capture_hotkey(self, key):
        self.unbind_hotkeys()
        self.log("Нажми новую клавишу или комбинацию...")

        def worker():
            try:
                combo = keyboard.read_hotkey(suppress=False)
            except Exception as e:
                combo = None
                self.log(f"Ошибка: {e}")
            self.post(self.set_hotkey, key, combo)

        threading.Thread(target=worker, daemon=True).start()

    def set_hotkey(self, key, combo):
        if combo:
            self.cfg[key] = combo
            self.hk_vars[key].set(combo)
            save_cfg(self.cfg)
            self._log(f"Хоткей назначен: {combo}")
        self.bind_hotkeys()

    # ----- управление -----
    def toggle_auto(self):
        self.auto = not self.auto
        self.armed = True
        if self.auto:
            self.tabbar.set_status("АВТО ВКЛ", C["teal"])
            self.btn_auto.set_text("ОСТАНОВИТЬ АВТО")
            self.btn_auto.set_kind("light")
        else:
            self.tabbar.set_status("АВТО ВЫКЛ", C["red"])
            self.btn_auto.set_text("ЗАПУСТИТЬ АВТО")
            self.btn_auto.set_kind("primary")
        self._log("Авто-режим: " + ("ВКЛ" if self.auto else "ВЫКЛ"))
        self.notify_auto(self.auto)
        if self.auto:   # на случай, если AHK-сервер закрыли - поднимаем заранее, до экрана выбора
            threading.Thread(target=self.ahk_ensure, daemon=True).start()

    def notify_auto(self, on):
        """Звук и плашка при включении/выключении авто-режима (видно и слышно, пока открыта игра)."""
        if self.cfg.get("notify_sound", True):
            threading.Thread(target=self._beep, args=(on,), daemon=True).start()
        if self.cfg.get("notify_popup", True):
            try:
                if self.toast is not None:
                    self.toast.close()
                hk = (self.cfg.get("hk_auto") or "").upper()
                self.toast = Toast(self, "АВТО-РЕЖИМ ВКЛЮЧЁН" if on else "АВТО-РЕЖИМ ВЫКЛЮЧЕН",
                                   f"{hk} - переключить" if hk else "", C["teal"] if on else C["red"])
            except Exception as e:
                self._log(f"Не удалось показать уведомление: {e}")

    @staticmethod
    def _beep(on):
        try:
            import winsound
            for freq in ((700, 1000) if on else (1000, 600)):
                winsound.Beep(freq, 90)
        except Exception:
            pass

    def update_lockin_label(self):
        p = self.cfg.get("lockin")
        self.lockin_lbl.config(text=f"кнопка выбора: {p[0]}, {p[1]}" if p else "кнопка выбора: по умолчанию")

    def set_lockin(self, pos):
        self.cfg["lockin"] = [int(pos[0]), int(pos[1])]
        save_cfg(self.cfg)
        self.update_lockin_label()
        self._log(f"Позиция кнопки выбора запомнена: {pos[0]},{pos[1]}")

    def lockin_button(self):
        self._log("Наведи курсор на кнопку выбора в игре - запомню через 4 секунды (или используй хоткей).")
        self.root.after(4000, lambda: self.set_lockin(pyautogui.position()))

    def pick_now_button(self):
        self._log("Окно свернётся, выбор начнётся через 1.5 с - переключись на Valorant.")
        self.root.iconify()
        self.root.after(1500, lambda: threading.Thread(target=self.do_pick, kwargs={"restore": True}, daemon=True).start())

    # ----- логика выбора -----
    def screen_is_select(self):
        if len(self.gray_small) < 2:
            return False
        g = Geo()
        region = cv2.cvtColor(grid_region(g, grab(g)), cv2.COLOR_BGR2GRAY)
        region = cv2.resize(region, None, fx=DETECT_SCALE, fy=DETECT_SCALE, interpolation=cv2.INTER_AREA)
        thresh = DETECT_THRESH if DETECT_SCALE >= 1.0 else DETECT_THRESH_SMALL
        order = self.hit_order + [i for i in range(len(self.gray_small)) if i not in self.hit_order]
        hits = []
        for i in order:
            if best_match(region, self.gray_small[i])[0] >= thresh:
                hits.append(i)
                if len(hits) >= 2:
                    self.hit_order = hits + [x for x in self.hit_order if x not in hits]
                    return True
        return False

    def scroll_list(self, g, to_top=False, down=0):
        """Прокрутка списка агентов колесом через постоянно запущенный AutoHotkey (pyautogui-колесо в игре не срабатывает)."""
        cx, cy = g.list_center
        nx, ny = g.neutral
        self.ahk.send(f"SCROLL|{cx}|{cy}|{nx}|{ny}|{int(bool(to_top))}|{int(down)}|{TOP_RESET_NOTCHES}")
        time.sleep(SCROLL_SETTLE)

    def scroll_to(self, g, cur, target):
        """Переводит список из положения cur в target (щелчков колеса от начала). Возвращает target."""
        if target == cur:
            return cur
        if target > cur:
            self.scroll_list(g, down=target - cur)
        else:
            self.scroll_list(g, to_top=True, down=target)   # вверх всегда через гарантированный сброс в начало
        return target

    def search_agent(self, g, name):
        region = grid_region(g, grab(g))
        t = self.templates[name]
        score, loc = best_match(region, t)
        if score >= self.cfg["thresh"]:
            return (g.mon["left"] + g.rx1 + loc[0] + t.shape[1] // 2,
                    g.mon["top"] + g.ry1 + loc[1] + t.shape[0] // 2)
        return None

    def find_agent(self, g, priority):
        clicks = max(1, int(self.cfg["scroll_clicks"]))
        cur = 0   # экран выбора открывается со списком в самом начале

        # 1) по запомненным прокруткам, в порядке приоритета
        for name in priority:
            target = self.scrolls.get(name, 0)
            cur = self.scroll_to(g, cur, target)
            pos = self.search_agent(g, name)
            if pos:
                return name, pos

        # 2) запасной вариант (данные устарели или агент ещё не запомнен): листаем сверху вниз
        prev = None
        for step in range(MAX_SCROLL_STEPS + 1):
            target = step * clicks
            cur = self.scroll_to(g, cur, target)
            for name in priority:
                pos = self.search_agent(g, name)
                if pos:
                    if self.scrolls.get(name) != target:
                        self.scrolls[name] = target
                        save_scrolls(self.scrolls)
                        self.log(f"Запомнил прокрутку для {name}: {target} щелчков вниз.")
                    return name, pos
            region = grid_region(g, grab(g))
            if prev is not None and same_region(region, prev):
                break   # дошли до конца списка
            prev = region
        return None, None

    def do_pick(self, restore=False):
        if not self.pick_lock.acquire(blocking=False):
            return
        try:
            g = Geo()
            priority = [n for n in self.cfg["priority"] if n in self.templates]
            if not priority:
                self.log("Приоритет пуст - добавь агентов в правый список.")
                return
            t0 = time.perf_counter()
            time.sleep(PICK_DELAY)   # короткая пауза, чтобы экран успел прорисоваться
            name, pos = self.find_agent(g, priority)
            if not pos:
                self.log("Ни один агент из списка не найден или недоступен. Попробуй ещё раз (хоткей/кнопка).")
                return
            lock = self.cfg.get("lockin")
            lock = tuple(lock) if lock else g.lockin_default
            self.ahk.send(f"PICK|{name}|{int(pos[0])}|{int(pos[1])}|{int(lock[0])}|{int(lock[1])}")
            self.log(f"Выбираю: {name} (команда в AHK через {int((time.perf_counter() - t0) * 1000)} мс)")
        except Exception as e:
            self.log(f"Ошибка выбора: {e}")
        finally:
            self.pick_lock.release()
            if restore:
                self.post(self.root.deiconify)

    def own_window_active(self):
        try:
            return bool(self.hwnd) and ctypes.windll.user32.GetForegroundWindow() == self.hwnd
        except Exception:
            return False

    def watcher(self):
        while True:
            time.sleep(POLL)
            if not self.auto:
                continue
            if self.own_window_active():   # не принимать свою же сетку иконок за экран выбора
                continue
            try:
                visible = self.screen_is_select()
            except Exception as e:
                self.log(f"Ошибка захвата: {e}")
                continue
            if visible and self.armed:
                self.armed = False            # один раз за экран выбора
                self.do_pick()
            elif not visible:
                self.armed = True             # экран пропал - готовы к следующему матчу


def write_crash(exc_type, exc, tb):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n"))
            f.write("".join(traceback.format_exception(exc_type, exc, tb)) + "\n")
    except Exception:
        pass


def main():
    sys.excepthook = write_crash
    msgs = init_data()
    root = tk.Tk()
    root.report_callback_exception = write_crash
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    init_fonts(root)
    init_style(root)
    App(root, msgs)
    root.mainloop()


if __name__ == "__main__":
    main()
