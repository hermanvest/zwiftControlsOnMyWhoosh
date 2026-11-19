"""Zwift controller BLE protocol: constants and message parsing.

Covers the Zwift Ride (smart frame controllers), Zwift Click v2 and the
original Zwift Click. All three speak the same unencrypted protocol once the
app writes "RideOn" to the sync RX characteristic:

* Notifications arrive on the async characteristic (and some on sync TX).
  Byte 0 is an opcode, the rest is a protobuf message.
* Opcode 0x23 carries ``RideKeyPadStatus``: field 1 is a 32-bit button bitmask
  where a *cleared* bit means "pressed" (all ones when idle), field 3 is a
  repeated ``RideAnalogKeyPress {1: location, 2: sint32 value}`` for the
  analog paddles.
* Opcode 0x37 (original Click) carries ``ClickKeyPadStatus``:
  ``{1: plus, 2: minus}`` where 0 means pressed.
* Opcode 0x19 carries battery state.

A Zwift Ride exposes two BLE peripherals (left and right hood). Only the left
one is connected to: it relays the right hood's buttons.

Protocol facts from ajchellew/zwiftplay, Makinolo's write-ups
(makinolo.com/blog/2024/07/26/zwift-ride-protocol/) and BikeControl
(github.com/OpenBikeControl/bikecontrol). This is an independent
implementation; no code is copied from those projects.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Iterator

ZWIFT_MANUFACTURER_ID = 0x094A  # Zwift, Inc.

ZWIFT_SERVICE_UUID = "00000001-19ca-4651-86e5-fa29dcdd09d1"
ZWIFT_RIDE_SERVICE_UUID = "0000fc82-0000-1000-8000-00805f9b34fb"
ASYNC_CHAR_UUID = "00000002-19ca-4651-86e5-fa29dcdd09d1"
SYNC_RX_CHAR_UUID = "00000003-19ca-4651-86e5-fa29dcdd09d1"
SYNC_TX_CHAR_UUID = "00000004-19ca-4651-86e5-fa29dcdd09d1"
FIRMWARE_REVISION_CHAR_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

RIDE_ON = b"RideOn"
# Extra start command BikeControl sends to a Zwift Ride V2 (firmware >= 1.3)
# once it has been unlocked by the Zwift app.
UNLOCKED_START = bytes([0xFF, 0x04, 0x00])

# Zwift Ride firmware from this version on ("Ride V2") hides its controller
# service until the official Zwift app has unlocked it (lasts about a day).
RIDE_V2_FIRMWARE = (1, 3, 0)

DEFAULT_PADDLE_THRESHOLD = 25  # analog paddle range is roughly -100..100


class DeviceType(enum.IntEnum):
    """First byte of the Zwift manufacturer data in the advertisement."""

    PLAY_RIGHT = 0x02
    PLAY_LEFT = 0x03
    RIDE_RIGHT = 0x07
    RIDE_LEFT = 0x08
    CLICK = 0x09
    CLICK_V2_RIGHT = 0x0A
    CLICK_V2_LEFT = 0x0B
    PLAY_FW2 = 0x0E


DEVICE_LABELS: dict[DeviceType, str] = {
    DeviceType.PLAY_RIGHT: "Zwift Play (right)",
    DeviceType.PLAY_LEFT: "Zwift Play (left)",
    DeviceType.RIDE_RIGHT: "Zwift Ride (right hood)",
    DeviceType.RIDE_LEFT: "Zwift Ride",
    DeviceType.CLICK: "Zwift Click",
    DeviceType.CLICK_V2_RIGHT: "Zwift Click v2 (right)",
    DeviceType.CLICK_V2_LEFT: "Zwift Click v2 (left)",
    DeviceType.PLAY_FW2: "Zwift Play",
}

# Device types we open a connection to. The Ride's right hood is skipped
# because the left hood relays its buttons. Zwift Play needs an encrypted
# handshake and isn't supported.
CONNECTABLE_TYPES = frozenset(
    {DeviceType.RIDE_LEFT, DeviceType.CLICK, DeviceType.CLICK_V2_LEFT, DeviceType.CLICK_V2_RIGHT}
)

# Advertised names we fall back to when manufacturer data is missing.
FALLBACK_NAMES = ("Zwift Ride", "Zwift Click")


class Opcode(enum.IntEnum):
    IDLE = 0x15
    BATTERY = 0x19
    KEYPAD = 0x23  # RideKeyPadStatus (Ride, Click v2)
    CLICK_KEYPAD = 0x37  # ClickKeyPadStatus (original Click)
    GET_RESPONSE = 0x3C
    STATUS_RESPONSE = 0x3E
    RIDE_ON = 0x52  # "R" of the "RideOn" handshake echo
    LOST_CONTROL = 0xFE
    VENDOR = 0xFF


class Button(enum.StrEnum):
    """Physical controls. Names follow Zwift's own RideButtonMask."""

    # left hood
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    SHIFT_UP_L = "SHIFT_UP_L"
    SHIFT_DN_L = "SHIFT_DN_L"
    POWERUP_L = "POWERUP_L"
    ONOFF_L = "ONOFF_L"
    PADDLE_L = "PADDLE_L"
    # right hood
    A = "A"
    B = "B"
    Y = "Y"
    Z = "Z"
    SHIFT_UP_R = "SHIFT_UP_R"
    SHIFT_DN_R = "SHIFT_DN_R"
    POWERUP_R = "POWERUP_R"
    ONOFF_R = "ONOFF_R"
    PADDLE_R = "PADDLE_R"


BUTTON_BITS: dict[Button, int] = {
    Button.LEFT: 0x00001,
    Button.UP: 0x00002,
    Button.RIGHT: 0x00004,
    Button.DOWN: 0x00008,
    Button.A: 0x00010,
    Button.B: 0x00020,
    Button.Y: 0x00040,
    Button.Z: 0x00080,
    Button.SHIFT_UP_L: 0x00100,
    Button.SHIFT_DN_L: 0x00200,
    Button.POWERUP_L: 0x00400,
    Button.ONOFF_L: 0x00800,
    Button.SHIFT_UP_R: 0x01000,
    Button.SHIFT_DN_R: 0x02000,
    Button.POWERUP_R: 0x04000,
    Button.ONOFF_R: 0x08000,
}

PADDLE_LOCATIONS: dict[int, Button] = {0: Button.PADDLE_L, 1: Button.PADDLE_R}

IDLE_BITMASK = 0xFFFFFFFF


# --- minimal protobuf wire-format helpers ---------------------------------


class DecodeError(ValueError):
    pass


def read_varint(buf: bytes, pos: int = 0) -> tuple[int, int]:
    """Decode a base-128 varint at ``pos``; returns (value, next_pos)."""
    value = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise DecodeError("truncated varint")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise DecodeError("varint too long")


def encode_varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def zigzag_decode(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def zigzag_encode(n: int) -> int:
    return (n << 1) ^ (n >> 63)


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield (field_number, wire_type, value) for each field in a protobuf message."""
    pos = 0
    while pos < len(buf):
        key, pos = read_varint(buf, pos)
        field_number, wire_type = key >> 3, key & 0x07
        if wire_type == 0:
            value, pos = read_varint(buf, pos)
        elif wire_type == 1:
            if pos + 8 > len(buf):
                raise DecodeError("truncated fixed64")
            value = int.from_bytes(buf[pos : pos + 8], "little")
            pos += 8
        elif wire_type == 2:
            length, pos = read_varint(buf, pos)
            if pos + length > len(buf):
                raise DecodeError("truncated length-delimited field")
            value = bytes(buf[pos : pos + length])
            pos += length
        elif wire_type == 5:
            if pos + 4 > len(buf):
                raise DecodeError("truncated fixed32")
            value = int.from_bytes(buf[pos : pos + 4], "little")
            pos += 4
        else:
            raise DecodeError(f"unsupported wire type {wire_type}")
        yield field_number, wire_type, value


# --- message parsing -------------------------------------------------------


@dataclass(frozen=True)
class KeypadState:
    pressed: frozenset[Button]
    paddles: dict[int, int] = field(default_factory=dict)  # location -> analog value


@dataclass(frozen=True)
class Message:
    opcode: int
    keypad: KeypadState | None = None
    battery: int | None = None
    handshake: bool = False


def parse_ride_keypad(payload: bytes, paddle_threshold: int = DEFAULT_PADDLE_THRESHOLD) -> KeypadState:
    bitmask = IDLE_BITMASK
    paddles: dict[int, int] = {}
    for number, wire_type, value in iter_fields(payload):
        if number == 1 and wire_type == 0:
            bitmask = int(value)
        elif number == 3 and wire_type == 2:
            location, analog = 0, 0
            for sub_number, sub_wire, sub_value in iter_fields(value):  # type: ignore[arg-type]
                if sub_number == 1 and sub_wire == 0:
                    location = int(sub_value)
                elif sub_number == 2 and sub_wire == 0:
                    analog = zigzag_decode(int(sub_value))
            paddles[location] = analog

    pressed = {button for button, bit in BUTTON_BITS.items() if not bitmask & bit}
    for location, analog in paddles.items():
        button = PADDLE_LOCATIONS.get(location)
        if button is not None and abs(analog) >= paddle_threshold:
            pressed.add(button)
    return KeypadState(frozenset(pressed), paddles)


def parse_click_keypad(payload: bytes) -> KeypadState:
    # proto3 omits default values, and 0 means "pressed", so a missing field is pressed.
    plus, minus = 0, 0
    for number, wire_type, value in iter_fields(payload):
        if wire_type != 0:
            continue
        if number == 1:
            plus = int(value)
        elif number == 2:
            minus = int(value)
    pressed = set()
    if plus == 0:
        pressed.add(Button.SHIFT_UP_R)
    if minus == 0:
        pressed.add(Button.SHIFT_UP_L)
    return KeypadState(frozenset(pressed))


def parse_battery(payload: bytes) -> int | None:
    """Battery level in percent.

    The Ride sends ``{1: charging_state, 2: percent}``; the original Click just
    ``{1: percent}``.
    """
    fields = {number: value for number, wire_type, value in iter_fields(payload) if wire_type == 0}
    level = fields.get(2, fields.get(1))
    if level is None or not 0 <= int(level) <= 100:
        return None
    return int(level)


def parse_message(data: bytes, paddle_threshold: int = DEFAULT_PADDLE_THRESHOLD) -> Message:
    """Parse one notification. Raises DecodeError on malformed protobuf."""
    data = bytes(data)
    if not data:
        raise DecodeError("empty message")
    if data.startswith(RIDE_ON):
        return Message(Opcode.RIDE_ON, handshake=True)
    opcode, payload = data[0], data[1:]
    if opcode == Opcode.KEYPAD:
        return Message(opcode, keypad=parse_ride_keypad(payload, paddle_threshold))
    if opcode == Opcode.CLICK_KEYPAD:
        return Message(opcode, keypad=parse_click_keypad(payload))
    if opcode == Opcode.BATTERY:
        return Message(opcode, battery=parse_battery(payload))
    return Message(opcode)


def encode_ride_keypad(pressed: set[Button] | frozenset[Button] = frozenset(), paddles: dict[int, int] | None = None) -> bytes:
    """Build a 0x23 keypad notification (used by tests and the simulator)."""
    bitmask = IDLE_BITMASK
    for button in pressed:
        bit = BUTTON_BITS.get(button)
        if bit:
            bitmask &= ~bit
    out = bytearray([Opcode.KEYPAD, 0x08]) + encode_varint(bitmask)
    for location, analog in (paddles or {}).items():
        sub = bytearray()
        if location:
            sub += b"\x08" + encode_varint(location)
        if analog:
            sub += b"\x10" + encode_varint(zigzag_encode(analog))
        out += b"\x1a" + encode_varint(len(sub)) + sub
    return bytes(out)


# --- advertisement / firmware helpers --------------------------------------


def device_type_from_manufacturer_data(manufacturer_data: dict[int, bytes]) -> DeviceType | None:
    data = manufacturer_data.get(ZWIFT_MANUFACTURER_ID)
    if not data:
        return None
    try:
        return DeviceType(data[0])
    except ValueError:
        return None


def parse_firmware_version(raw: str | None) -> tuple[int, int, int] | None:
    """Leading major.minor.patch of a firmware string such as '1.2.0.24'."""
    if not raw:
        return None
    match = re.match(r"\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?", raw)
    if not match:
        return None
    major, minor, patch = (int(part) if part else 0 for part in match.groups())
    return major, minor, patch


def is_ride_v2_firmware(raw: str | None) -> bool:
    version = parse_firmware_version(raw)
    return version is not None and version >= RIDE_V2_FIRMWARE
