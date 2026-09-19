"""Hands and eyes. Real keycodes (Electron date and table inputs ignore unicode-only events),
Vision OCR through typesafe-computer-use, and window-scoped captures."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import Quartz
from typesafe_computer_use import macos, perception

US = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46,
    ".": 47, " ": 49, "`": 50,
}
SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))
KEYS = {"return": 36, "tab": 48, "escape": 53, "delete": 51, "down": 125, "up": 126, "left": 123, "right": 124}


def grab():
    """The main display straight from the window server, about 30 ms. The screencapture CLI costs 200 ms a shot."""
    from PIL import Image

    img = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
    w, h, bpr = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img), Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    return Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", bpr, 1).convert("RGB")


macos.screenshot = grab  # perception.capture() goes through this name


def _key(code: int, flags: int = 0) -> None:
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.004)


def type_text(text: str, per_char: float = 0.006) -> None:
    for ch in text:
        low = ch.lower()
        if ch in SHIFTED:
            _key(US[SHIFTED[ch]], Quartz.kCGEventFlagMaskShift)
        elif low in US:
            _key(US[low], Quartz.kCGEventFlagMaskShift if ch != low else 0)
        else:  # anything off the US layout goes as a unicode event
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(per_char)


def press(name: str, command: bool = False, shift: bool = False) -> None:
    code = KEYS.get(name, US.get(name))
    flags = (Quartz.kCGEventFlagMaskCommand if command else 0) | (Quartz.kCGEventFlagMaskShift if shift else 0)
    _key(code, flags)


def click(x: float, y: float, double: bool = False) -> None:
    macos.click_at((x, y))
    if double:
        time.sleep(0.05)
        macos.click_at((x, y))


def scroll(lines: int) -> None:
    macos.scroll(lines)


def activate(app: str) -> None:
    macos.activate(app)


def abort_check() -> None:
    macos.check_abort()


@dataclass(frozen=True)
class Line:
    i: int
    text: str
    x: float  # centre, screen points
    y: float
    w: float
    h: float


@dataclass
class View:
    app: str
    lines: list[Line]
    seconds: float
    image: object = None


def look(app: str | None = None) -> View:
    """OCR the frontmost window. Lines come back in reading order, numbered from 0."""
    started = time.perf_counter()
    screen = perception.capture()
    raw, _, _ = perception.ocr_lines(screen, None)
    s = screen.scale
    win = screen.window
    boxes = []
    for text, conf, (x1, y1, x2, y2) in raw:
        cx, cy = (x1 + x2) / 2 / s, (y1 + y2) / 2 / s
        if win and not (win[0] <= cx <= win[0] + win[2] and win[1] <= cy <= win[1] + win[3]):
            continue
        if len(text.strip()) < 1:
            continue
        boxes.append((round(cy / 9), cx, text.strip(), cx, cy, (x2 - x1) / s, (y2 - y1) / s))
    boxes.sort(key=lambda b: (b[0], b[1]))
    lines = [Line(i, t, x, y, w, h) for i, (_, _, t, x, y, w, h) in enumerate(boxes)]
    return View(app=screen.app, lines=lines, seconds=time.perf_counter() - started, image=screen.image)


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout


def settle(limit: float, floor: float = 0.12) -> float:
    """Wait until the screen stops changing instead of sleeping a fixed time. Returns the seconds spent.

    `limit` stays the ceiling System 2 tunes; a screen that is already still costs two quick grabs."""
    from PIL import ImageChops

    started = time.perf_counter()
    time.sleep(floor)
    last, still = None, 0
    while time.perf_counter() - started < limit:
        thumb = grab().convert("L").resize((192, 124))
        if last is not None and sum(ImageChops.difference(thumb, last).histogram()[20:]) <= 6:
            still += 1
            if still >= 2:
                break
        else:
            still = 0
        last = thumb
        time.sleep(0.04)
    return time.perf_counter() - started
