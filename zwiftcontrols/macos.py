"""macOS integration: synthetic key events, permissions and MyWhoosh focus.

Keys are posted as Quartz keyboard events by *virtual key code* (physical key
position), which is what games read. Posting requires the terminal app to be
allowed under System Settings → Privacy & Security → Accessibility.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time

import Quartz

logger = logging.getLogger(__name__)

MYWHOOSH_BUNDLE_ID = "com.whoosh.whooshgame"
_MYWHOOSH_PROCESS_PATTERN = r"MyWhoosh\.app/Contents/(MacOS|UE)/"

# macOS virtual key codes (Carbon kVK_*), ANSI positions.
KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07,
    "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10,
    "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18,
    "9": 0x19, "7": 0x1A, "-": 0x1B, "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F, "u": 0x20,
    "[": 0x21, "i": 0x22, "p": 0x23, "return": 0x24, "l": 0x25, "j": 0x26, "'": 0x27, "k": 0x28,
    ";": 0x29, "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E, ".": 0x2F, "tab": 0x30,
    "space": 0x31, "`": 0x32, "backspace": 0x33, "escape": 0x35,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62,
    "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "home": 0x73, "pageup": 0x74, "delete": 0x75, "end": 0x77, "pagedown": 0x79,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
}

KEY_LABELS: dict[str, str] = {
    "return": "⏎ Return", "escape": "Esc", "space": "Space", "tab": "Tab",
    "left": "←", "right": "→", "up": "↑", "down": "↓",
}

_ARROWS = {"left", "right", "up", "down"}
_ARROW_FLAGS = Quartz.kCGEventFlagMaskNumericPad | Quartz.kCGEventFlagMaskSecondaryFn

TAP_HOLD_SECONDS = 0.03  # key down→up gap, long enough for a game frame to see it


def key_label(key: str | None) -> str:
    if not key:
        return "—"
    return KEY_LABELS.get(key, key.upper())


# --- permissions ----------------------------------------------------------


def post_access_granted() -> bool:
    """Whether this process (i.e. the terminal app) may post keyboard events."""
    preflight = getattr(Quartz, "CGPreflightPostEventAccess", None)
    return bool(preflight()) if preflight else True


def request_post_access() -> bool:
    """Show the system Accessibility prompt if access hasn't been granted yet."""
    request = getattr(Quartz, "CGRequestPostEventAccess", None)
    return bool(request()) if request else True


def bluetooth_authorization() -> str:
    try:
        import CoreBluetooth

        status = int(CoreBluetooth.CBManager.authorization())
    except Exception:  # pragma: no cover - very old macOS / missing framework
        return "unknown"
    return {0: "not determined", 1: "restricted", 2: "denied", 3: "allowed"}.get(status, "unknown")


def open_privacy_settings(pane: str) -> None:
    """pane: 'Accessibility' or 'Bluetooth'."""
    subprocess.Popen(
        ["open", f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# --- MyWhoosh process / window ---------------------------------------------

_pid_cache: tuple[float, list[int]] = (0.0, [])


def mywhoosh_pids(max_age: float = 2.0) -> list[int]:
    """PIDs of running MyWhoosh processes (cached for ``max_age`` seconds)."""
    global _pid_cache
    stamp, pids = _pid_cache
    now = time.monotonic()
    if now - stamp < max_age:
        return pids
    try:
        out = subprocess.run(
            ["pgrep", "-f", _MYWHOOSH_PROCESS_PATTERN], capture_output=True, text=True, timeout=2
        ).stdout
        pids = [int(line) for line in out.split()]
    except (OSError, subprocess.SubprocessError, ValueError):
        pids = []
    _pid_cache = (now, pids)
    return pids


def _forget_pids() -> None:
    global _pid_cache
    _pid_cache = (0.0, [])


def frontmost_pid() -> int | None:
    """PID owning the frontmost normal window (live, unlike NSWorkspace without a run loop)."""
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for window in windows or []:
        if window.get("kCGWindowLayer") == 0:
            return int(window.get("kCGWindowOwnerPID"))
    return None


def mywhoosh_state() -> tuple[bool, bool]:
    """(running, frontmost)."""
    pids = mywhoosh_pids()
    if not pids:
        return False, False
    return True, frontmost_pid() in pids


def mywhoosh_window_pid() -> int | None:
    """The MyWhoosh PID that owns windows (the one to post background events to)."""
    pids = mywhoosh_pids()
    if not pids:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    for window in windows or []:
        pid = int(window.get("kCGWindowOwnerPID", -1))
        if pid in pids and window.get("kCGWindowLayer") == 0:
            return pid
    return pids[0]


async def activate_mywhoosh(timeout: float = 1.5) -> bool:
    """Bring MyWhoosh to the front. Returns True once it is frontmost.

    Never launches MyWhoosh: the (cached) PID list is refreshed first so a
    just-quit game isn't reopened by ``open -b``.
    """
    _forget_pids()
    if not mywhoosh_pids():
        return False
    proc = await asyncio.create_subprocess_exec(
        "open", "-b", MYWHOOSH_BUNDLE_ID, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _forget_pids()
        if mywhoosh_state()[1]:
            await asyncio.sleep(0.05)  # let the window become key before typing into it
            return True
        await asyncio.sleep(0.05)
    return False


# --- key events -------------------------------------------------------------


def post_key(key: str, down: bool, pid: int | None = None) -> None:
    code = KEYCODES[key]
    event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
    # Don't inherit whatever modifiers are physically held; arrows carry the
    # numpad/fn flags a real keyboard sends.
    Quartz.CGEventSetFlags(event, _ARROW_FLAGS if key in _ARROWS else 0)
    if pid is None:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    else:
        Quartz.CGEventPostToPid(pid, event)


class KeySender:
    """Sends key presses to MyWhoosh according to a delivery mode.

    * ``focus`` (default): only ever types into MyWhoosh. If another app is in
      front, MyWhoosh is brought forward first. Nothing is sent when MyWhoosh
      isn't running, so a stray button press can't type into another app.
    * ``background``: posts events straight to the MyWhoosh process without
      focusing it (experimental; depends on MyWhoosh accepting them).
    * ``frontmost``: types into whatever app is in front (testing, other apps).
    """

    def __init__(self, delivery: str = "focus"):
        self.delivery = delivery

    async def send(self, key: str, phase: str) -> tuple[bool, str]:
        """phase: 'tap', 'down' or 'up'. Returns (sent, note)."""
        if key not in KEYCODES:
            return False, f"unknown key {key!r}"
        if not post_access_granted():
            return False, "no Accessibility permission for this terminal"

        pid: int | None = None
        note = ""
        if self.delivery == "focus":
            running, front = mywhoosh_state()
            if not running:
                return False, "MyWhoosh isn't running"
            if not front and phase != "up":
                if not await activate_mywhoosh():
                    return False, "couldn't bring MyWhoosh to the front"
                note = "focused MyWhoosh"
        elif self.delivery == "background":
            pid = mywhoosh_window_pid()
            if pid is None:
                return False, "MyWhoosh isn't running"

        if phase in ("tap", "down"):
            post_key(key, True, pid)
        if phase == "tap":
            await asyncio.sleep(TAP_HOLD_SECONDS)
        if phase in ("tap", "up"):
            post_key(key, False, pid)
        return True, note
