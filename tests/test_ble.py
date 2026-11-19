import asyncio

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from zwiftcontrols.ble import DeviceManager
from zwiftcontrols.protocol import Button, DeviceType, encode_ride_keypad


def advert(address, name, manufacturer_data):
    device = BLEDevice(address, name, None)
    adv = AdvertisementData(
        local_name=name, manufacturer_data=manufacturer_data, service_data={}, service_uuids=[],
        tx_power=None, rssi=-60, platform_data=(),
    )
    return device, adv


def make_manager():
    keypads, devices = [], []
    manager = DeviceManager(on_keypad=lambda a, s: keypads.append((a, s)), on_device=devices.append, on_disconnect=lambda a: None)
    connects = []

    async def fake_connect(device, info):
        connects.append(device.address)
        manager._tasks.pop(device.address, None)

    manager._connect = fake_connect
    return manager, connects, keypads


async def test_connects_left_hood_and_skips_right_hood():
    manager, connects, _ = make_manager()
    manager._on_detect(*advert("LEFT", "Zwift Ride", {0x094A: bytes([0x08, 0x13])}))
    manager._on_detect(*advert("RIGHT", "Zwift Ride", {0x094A: bytes([0x07, 0x13])}))
    manager._on_detect(*advert("PHONE", "iPhone", {0x004C: b"\x10"}))
    await asyncio.sleep(0)
    assert connects == ["LEFT"]
    assert manager.devices["RIGHT"].status == "skipped"
    assert "PHONE" not in manager.devices
    assert manager.devices["LEFT"].device_type == DeviceType.RIDE_LEFT


async def test_click_by_name_without_manufacturer_data():
    manager, connects, _ = make_manager()
    manager._on_detect(*advert("CLICK", "Zwift Click", {}))
    await asyncio.sleep(0)
    assert connects == ["CLICK"]


async def test_no_duplicate_connect_while_connecting():
    manager, connects, _ = make_manager()
    manager._tasks["LEFT"] = asyncio.get_running_loop().create_future()  # pretend in progress
    manager._on_detect(*advert("LEFT", "Zwift Ride", {0x094A: bytes([0x08])}))
    await asyncio.sleep(0)
    assert connects == []


def test_notifications_route_keypad_and_battery():
    manager, _, keypads = make_manager()
    from zwiftcontrols.ble import DeviceInfo

    info = DeviceInfo("LEFT", "Zwift Ride", DeviceType.RIDE_LEFT)
    manager._on_notify(info, bytearray(encode_ride_keypad({Button.A})))
    manager._on_notify(info, bytearray.fromhex("19 08 00 10 3c"))
    manager._on_notify(info, bytearray.fromhex("23 08 ff"))  # truncated: ignored
    assert [(a, s.pressed) for a, s in keypads] == [("LEFT", {Button.A})]
    assert info.battery == 60
