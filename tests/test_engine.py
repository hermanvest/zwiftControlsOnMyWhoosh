import asyncio
import json


from zwiftcontrols.config import Config
from zwiftcontrols.engine import Bridge, ButtonHub
from zwiftcontrols.protocol import Button, KeypadState


class FakeKeys:
    def __init__(self):
        self.delivery = "focus"
        self.sent: list[tuple[str, str]] = []

    async def send(self, key, phase):
        self.sent.append((key, phase))
        return True, ""


def make_bridge(tmp_path, **config_kwargs):
    config = Config(path=tmp_path / "config.json", **config_kwargs)
    keys = FakeKeys()
    events = []
    bridge = Bridge(config, key_sender=keys, on_action=events.append)
    bridge.start_worker()
    return bridge, keys, events


def press(bridge, *buttons, device="dev1"):
    bridge.handle_keypad(device, KeypadState(frozenset(buttons)))


async def settle(seconds=0.05):
    await asyncio.sleep(seconds)


def test_hub_merges_devices():
    log = []
    hub = ButtonHub(lambda b: log.append(("down", b)), lambda b: log.append(("up", b)))
    hub.update("left", frozenset({Button.A}))
    hub.update("right", frozenset({Button.A}))
    hub.update("left", frozenset())
    assert log == [("down", Button.A)]
    hub.update("right", frozenset())
    assert log == [("down", Button.A), ("up", Button.A)]


def test_hub_drop_releases_buttons():
    log = []
    hub = ButtonHub(lambda b: log.append(("down", b)), lambda b: log.append(("up", b)))
    hub.update("dev", frozenset({Button.LEFT, Button.B}))
    hub.drop("dev")
    assert sorted(log) == sorted([("down", Button.LEFT), ("down", Button.B), ("up", Button.LEFT), ("up", Button.B)])
    assert hub.pressed == frozenset()


async def test_shift_buttons_tap_k_and_i(tmp_path):
    bridge, keys, events = make_bridge(tmp_path)
    press(bridge, Button.SHIFT_UP_R)
    press(bridge)
    press(bridge, Button.PADDLE_L)
    press(bridge)
    await settle()
    assert keys.sent == [("k", "tap"), ("i", "tap")]
    assert [e.action.id for e in events] == ["gear_harder", "gear_easier"]


async def test_swap_shift_keys(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path, swap_shift_keys=True)
    press(bridge, Button.SHIFT_UP_R)
    press(bridge)
    await settle()
    assert keys.sent == [("i", "tap")]


async def test_menu_buttons(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    for button in (Button.DOWN, Button.A, Button.B, Button.Y):
        press(bridge, button)
        press(bridge)
    await settle()
    assert keys.sent == [("down", "tap"), ("return", "tap"), ("escape", "tap"), ("escape", "tap")]


async def test_dpad_left_is_held_for_steering(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    press(bridge, Button.LEFT)
    await settle()
    assert keys.sent == [("left", "down")]
    press(bridge)
    await settle()
    assert keys.sent == [("left", "down"), ("left", "up")]


async def test_disconnect_releases_held_key(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    press(bridge, Button.RIGHT)
    bridge.hub.drop("dev1")
    await settle()
    assert keys.sent == [("right", "down"), ("right", "up")]


async def test_holding_shift_repeats(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path, repeat_delay=0.05, repeat_interval=0.02)
    press(bridge, Button.SHIFT_UP_R)
    await settle(0.16)
    press(bridge)
    count = len(keys.sent)
    await settle(0.08)
    assert count >= 3
    assert len(keys.sent) == count  # stopped after release
    assert set(keys.sent) == {("k", "tap")}


async def test_output_paused_sends_nothing(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    bridge.output_enabled = False
    press(bridge, Button.SHIFT_UP_R)
    press(bridge)
    await settle()
    assert keys.sent == []


async def test_remapped_and_unmapped_buttons(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    bridge.config.mapping[Button.Z] = "none"
    bridge.config.mapping[Button.A] = "emote_2"
    press(bridge, Button.Z)
    press(bridge)
    press(bridge, Button.A)
    press(bridge)
    await settle()
    assert keys.sent == [("2", "tap")]


async def test_link_only_action_without_link(tmp_path):
    bridge, keys, events = make_bridge(tmp_path)
    bridge.config.mapping[Button.Z] = "uturn"
    press(bridge, Button.Z)
    press(bridge)
    await settle()
    assert keys.sent == []
    assert events[-1].sent is False and "Link" in events[-1].note


async def test_link_routes_shifting_and_steering(tmp_path):
    bridge, keys, _ = make_bridge(tmp_path)
    bridge.link.port = 0
    bridge.link.host = "127.0.0.1"
    await bridge.link.start()
    port = bridge.link._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    await settle()
    assert bridge.link.connected

    bridge.config.mapping[Button.LEFT] = "steer_left"
    bridge.config.mapping[Button.Z] = "camera"
    press(bridge, Button.SHIFT_UP_R)
    press(bridge)
    press(bridge, Button.LEFT)
    press(bridge)
    press(bridge, Button.Z)
    press(bridge)
    press(bridge, Button.A)  # select has no Link equivalent -> keyboard
    press(bridge)
    await settle()

    lines = [json.loads(await asyncio.wait_for(reader.readline(), 1)) for _ in range(4)]
    controls = [line["InGameControls"] for line in lines]
    assert all(line["MessageType"] == "Controls" for line in lines)
    assert controls == [{"GearShifting": "1"}, {"Steering": "-1"}, {"Steering": "0"}, {"CameraAngle": "1"}]
    assert keys.sent == [("return", "tap")]

    writer.close()
    await bridge.link.stop()


def test_config_round_trip(tmp_path):
    config = Config(path=tmp_path / "c.json")
    config.mapping[Button.Y] = "emote_1"
    config.swap_shift_keys = True
    config.delivery = "background"
    config.key_overrides["hide_hud"] = "f5"
    config.save()
    loaded = Config.load(tmp_path / "c.json")
    assert loaded.mapping[Button.Y] == "emote_1"
    assert loaded.swap_shift_keys and loaded.delivery == "background"
    assert loaded.key_overrides == {"hide_hud": "f5"}


def test_config_ignores_garbage(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"mapping": {"NOPE": "select", "A": "nope"}, "keys": {"select": "notakey"}, "delivery": "x"}))
    loaded = Config.load(path)
    assert loaded.mapping[Button.A] == "select"
    assert loaded.key_overrides == {}
    assert loaded.delivery == "focus"
