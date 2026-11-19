import pytest

from zwiftcontrols.protocol import (
    Button,
    DecodeError,
    DeviceType,
    encode_ride_keypad,
    is_ride_v2_firmware,
    parse_firmware_version,
    parse_message,
    device_type_from_manufacturer_data,
)


def msg(hex_string: str):
    return parse_message(bytes.fromhex(hex_string))


def test_idle_keypad_has_no_buttons():
    assert msg("23 08 ff ff ff ff 0f").keypad.pressed == frozenset()


def test_right_shift_button_pressed():
    # bitmask 0xFFFFEFFF: bit 0x1000 (SHIFT_UP_R, the Click "+") cleared
    assert msg("23 08 ff df ff ff 0f").keypad.pressed == {Button.SHIFT_UP_R}


def test_left_shift_button_pressed():
    # bitmask 0xFFFFFEFF: bit 0x100 (SHIFT_UP_L, the Click "-") cleared
    assert msg("23 08 ff fd ff ff 0f").keypad.pressed == {Button.SHIFT_UP_L}


def test_dpad_and_a_pressed_together():
    # 0xFFFFFFEE: LEFT (0x1) and A (0x10) cleared
    assert msg("23 08 ee ff ff ff 0f").keypad.pressed == {Button.LEFT, Button.A}


def test_analog_paddle_over_threshold_counts_as_press():
    # field 3: {location omitted (=0, left), value zigzag(-100)=199}
    state = msg("23 08 ff ff ff ff 0f 1a 03 10 c7 01").keypad
    assert state.paddles == {0: -100}
    assert state.pressed == {Button.PADDLE_L}


def test_analog_paddle_right_and_small_values_ignored():
    # right paddle (location 1) at +10, below the threshold of 25
    state = msg("23 08 ff ff ff ff 0f 1a 04 08 01 10 14").keypad
    assert state.paddles == {1: 10}
    assert state.pressed == frozenset()


def test_paddle_at_rest_is_released():
    # four paddle entries at value 0 (left one is an empty sub-message)
    state = msg("23 08 ff ff ff ff 0f 1a 00 1a 02 08 01 1a 02 08 02 1a 02 08 03").keypad
    assert state.pressed == frozenset()


def test_encoder_round_trip():
    pressed = {Button.Y, Button.SHIFT_DN_R, Button.ONOFF_R}
    state = parse_message(encode_ride_keypad(pressed, {1: 80})).keypad
    assert state.pressed == pressed | {Button.PADDLE_R}


def test_original_click_plus_and_minus():
    assert msg("37 08 00 10 01").keypad.pressed == {Button.SHIFT_UP_R}
    assert msg("37 08 01 10 00").keypad.pressed == {Button.SHIFT_UP_L}
    assert msg("37 08 01 10 01").keypad.pressed == frozenset()
    # proto3 drops default (0 = pressed) values: missing field means pressed
    assert msg("37 10 01").keypad.pressed == {Button.SHIFT_UP_R}


def test_battery_formats():
    assert msg("19 08 00 10 5a").battery == 90  # Ride: {charging state, percent}
    assert msg("19 10 4b").battery == 75
    assert msg("19 08 50").battery == 80  # original Click: {percent}


def test_handshake_and_unknown():
    assert parse_message(b"RideOn\x02\x03").handshake
    assert msg("15").keypad is None


def test_truncated_message_raises():
    with pytest.raises(DecodeError):
        msg("23 08 ff ff")


def test_device_type_and_firmware():
    assert device_type_from_manufacturer_data({0x094A: bytes([0x08, 0x01])}) == DeviceType.RIDE_LEFT
    assert device_type_from_manufacturer_data({0x094A: bytes([0x07])}) == DeviceType.RIDE_RIGHT
    assert device_type_from_manufacturer_data({0x004C: b"\x02"}) is None
    assert parse_firmware_version("1.2.0.24") == (1, 2, 0)
    assert not is_ride_v2_firmware("1.2.0")
    assert is_ride_v2_firmware("1.3.0")
    assert not is_ride_v2_firmware(None)
