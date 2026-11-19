import asyncio

from rich.text import Text

from zwiftcontrols.ble import DeviceInfo
from zwiftcontrols.config import Config
from zwiftcontrols.engine import Bridge
from zwiftcontrols.protocol import Button, DeviceType, KeypadState
from zwiftcontrols.tui import ActionPicker, ZwhooshApp

from test_engine import FakeKeys


def make_app(tmp_path):
    config = Config(path=tmp_path / "config.json")
    keys = FakeKeys()
    app = ZwhooshApp(config, start_bridge=False)
    app.bridge = Bridge(
        config,
        key_sender=keys,
        on_device=app.update_device,
        on_button=app.show_button,
        on_action=app.show_action,
        on_status=app._on_bridge_status,
    )
    return app, config, keys


def cell(app, button, column):
    value = app.query_one("#mapping").get_cell(button.value, column)
    return value.plain if isinstance(value, Text) else str(value)


async def test_live_highlight_device_row_and_actions(tmp_path):
    app, config, keys = make_app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        app.bridge.start_worker()
        info = DeviceInfo("ABCD-1234", "Zwift Ride", DeviceType.RIDE_LEFT, status="connected", battery=77, firmware="1.2.0")
        app.bridge.devices.devices[info.address] = info
        app.update_device(info)
        info.battery = 76
        app.update_device(info)  # second update edits the existing row
        await pilot.pause()
        assert app.query_one("#devices").row_count == 1
        assert app.query_one("#devices").get_cell(info.address, "battery").plain == "76%"
        assert "Ready" in str(app.query_one("#hint").render())

        app.bridge.handle_keypad("ABCD-1234", KeypadState(frozenset({Button.SHIFT_UP_R})))
        await pilot.pause()
        assert cell(app, Button.SHIFT_UP_R, "live") == "●"
        app.bridge.handle_keypad("ABCD-1234", KeypadState(frozenset()))
        await asyncio.sleep(0.05)
        await pilot.pause()
        assert cell(app, Button.SHIFT_UP_R, "live") == ""
        assert keys.sent == [("k", "tap")]

        await pilot.press("x")  # swap I/K
        assert config.swap_shift_keys
        assert cell(app, Button.SHIFT_UP_R, "key") == "I"
        assert Config.load(config.path).swap_shift_keys

        await pilot.press("o")  # pause output
        app.bridge.handle_keypad("ABCD-1234", KeypadState(frozenset({Button.A})))
        app.bridge.handle_keypad("ABCD-1234", KeypadState(frozenset()))
        await asyncio.sleep(0.05)
        assert keys.sent == [("k", "tap")]


async def test_remap_via_picker(tmp_path):
    app, config, keys = make_app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        mapping = app.query_one("#mapping")
        mapping.focus()
        mapping.move_cursor(row=mapping.get_row_index(Button.Z.value))
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ActionPicker)
        options = app.screen.query_one("OptionList")
        target = next(i for i in range(options.option_count) if options.get_option_at_index(i).id == "uturn")
        options.highlighted = target
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ActionPicker)
        assert config.mapping[Button.Z] == "uturn"
        assert cell(app, Button.Z, "action").startswith("U-turn")
        assert Config.load(config.path).mapping[Button.Z] == "uturn"


async def test_locked_ride_shows_unlock_hint(tmp_path):
    app, _, _ = make_app(tmp_path)
    async with app.run_test(size=(140, 45)) as pilot:
        info = DeviceInfo("X", "Zwift Ride", DeviceType.RIDE_LEFT, status="locked", firmware="1.3.0",
                          detail="Locked to Zwift (firmware 1.3.0). To unlock: open the Zwift app")
        app.bridge.devices.devices["X"] = info
        app.update_device(info)
        await pilot.pause()
        assert "Zwift Ride V2" in str(app.query_one("#hint").render())
