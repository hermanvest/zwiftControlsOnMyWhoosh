"""Find and connect Zwift controllers over Bluetooth LE (CoreBluetooth via bleak).

The manager scans continuously, connects to every supported controller it
sees and reconnects automatically when one wakes up again after sleeping.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from zwiftcontrols.protocol import (
    ASYNC_CHAR_UUID,
    BATTERY_LEVEL_CHAR_UUID,
    CONNECTABLE_TYPES,
    DEFAULT_PADDLE_THRESHOLD,
    DEVICE_LABELS,
    FALLBACK_NAMES,
    FIRMWARE_REVISION_CHAR_UUID,
    RIDE_ON,
    SYNC_RX_CHAR_UUID,
    SYNC_TX_CHAR_UUID,
    UNLOCKED_START,
    DecodeError,
    DeviceType,
    KeypadState,
    device_type_from_manufacturer_data,
    is_ride_v2_firmware,
    parse_message,
)

logger = logging.getLogger(__name__)

SCAN_WINDOW = 15.0  # CoreBluetooth reports each peripheral once per scan; restart to see it again
CONNECT_TIMEOUT = 20.0
RETRY_DELAY = 5.0
LOCKED_RETRY_DELAY = 60.0

UNLOCK_HINT = (
    "open the Zwift app (free account is enough), pair your trainer and the controller, "
    "ride ~30 s, quit Zwift completely, then press a controller button and 'c' in zwhoosh. "
    "The unlock lasts about a day."
)


@dataclass
class DeviceInfo:
    address: str
    name: str
    device_type: DeviceType | None
    status: str = "seen"  # seen | skipped | connecting | connected | locked | error | asleep
    battery: int | None = None
    firmware: str | None = None
    rssi: int | None = None
    detail: str = ""

    @property
    def label(self) -> str:
        if self.device_type == DeviceType.RIDE_LEFT and is_ride_v2_firmware(self.firmware):
            return "Zwift Ride V2"
        if self.device_type is not None:
            return DEVICE_LABELS[self.device_type]
        return self.name or "Zwift controller"


def _locked_detail(info: DeviceInfo) -> str:
    firmware = info.firmware or "unknown"
    if info.device_type == DeviceType.CLICK_V2_LEFT or info.device_type == DeviceType.CLICK_V2_RIGHT:
        return f"Locked to Zwift (Click v2, firmware {firmware}). To unlock: {UNLOCK_HINT}"
    if info.device_type == DeviceType.RIDE_LEFT and not is_ride_v2_firmware(info.firmware):
        return (
            f"Controller service missing (firmware {firmware}). Update the Ride's firmware with the "
            "Zwift Companion app."
        )
    return f"Locked to Zwift (firmware {firmware}). To unlock: {UNLOCK_HINT}"


class DeviceManager:
    def __init__(
        self,
        *,
        on_keypad: Callable[[str, KeypadState], None],
        on_device: Callable[[DeviceInfo], None],
        on_disconnect: Callable[[str], None],
        paddle_threshold: Callable[[], int] = lambda: DEFAULT_PADDLE_THRESHOLD,
        raw_debug: bool = False,
    ):
        self.on_keypad = on_keypad
        self.on_device = on_device
        self.on_disconnect = on_disconnect
        self.paddle_threshold = paddle_threshold
        self.raw_debug = raw_debug
        self.devices: dict[str, DeviceInfo] = {}
        self.scanning = False
        self.scan_error: str | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._clients: dict[str, BleakClient] = {}
        self._retry_at: dict[str, float] = {}
        self._stop = asyncio.Event()
        self._rescan = asyncio.Event()

    # --- scanning ---

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                async with BleakScanner(detection_callback=self._on_detect):
                    self.scanning = True
                    self.scan_error = None
                    self._rescan.clear()
                    waiters = [asyncio.create_task(self._stop.wait()), asyncio.create_task(self._rescan.wait())]
                    try:
                        await asyncio.wait(waiters, timeout=SCAN_WINDOW, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for waiter in waiters:
                            waiter.cancel()
            except (BleakError, OSError) as exc:
                self.scanning = False
                message = str(exc) or type(exc).__name__
                if message != self.scan_error:
                    logger.error("Bluetooth scan failed: %s", message)
                self.scan_error = message
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3)
                except asyncio.TimeoutError:
                    pass
        self.scanning = False

    def rescan(self) -> None:
        self._rescan.set()

    def _on_detect(self, device: BLEDevice, adv: AdvertisementData) -> None:
        device_type = device_type_from_manufacturer_data(adv.manufacturer_data)
        name = adv.local_name or device.name or ""
        if device_type is None and name not in FALLBACK_NAMES:
            return

        info = self.devices.get(device.address)
        if info is None:
            info = DeviceInfo(device.address, name, device_type)
            self.devices[device.address] = info
            logger.info("Found %s (%s)", info.label, device.address[:8])
        info.rssi = adv.rssi
        if name:
            info.name = name

        connectable = device_type in CONNECTABLE_TYPES or (device_type is None and name in FALLBACK_NAMES)
        if not connectable:
            if info.status == "seen":
                if device_type == DeviceType.RIDE_RIGHT:
                    detail = "buttons are relayed through the left hood"
                else:
                    detail = "not supported"
                self._set(info, "skipped", detail)
            return

        if device.address in self._tasks or time.monotonic() < self._retry_at.get(device.address, 0):
            return
        self._tasks[device.address] = asyncio.create_task(self._connect(device, info))

    # --- connections ---

    def _set(self, info: DeviceInfo, status: str, detail: str | None = None) -> None:
        info.status = status
        if detail is not None:
            info.detail = detail
        self.on_device(info)

    async def _connect(self, device: BLEDevice, info: DeviceInfo) -> None:
        address = device.address
        disconnected = asyncio.Event()
        client = BleakClient(device, disconnected_callback=lambda _client: disconnected.set(), timeout=CONNECT_TIMEOUT)
        self._set(info, "connecting", "")
        try:
            await client.connect()
            self._clients[address] = client

            info.firmware = await self._read_text(client, FIRMWARE_REVISION_CHAR_UUID) or info.firmware
            battery = await self._read_bytes(client, BATTERY_LEVEL_CHAR_UUID)
            if battery:
                info.battery = battery[0]

            async_char = client.services.get_characteristic(ASYNC_CHAR_UUID)
            rx_char = client.services.get_characteristic(SYNC_RX_CHAR_UUID)
            if async_char is None or rx_char is None:
                self._retry_at[address] = time.monotonic() + LOCKED_RETRY_DELAY
                self._set(info, "locked", _locked_detail(info))
                logger.warning("%s: %s", info.label, info.detail)
                return

            def on_notify(_char: BleakGATTCharacteristic, data: bytearray) -> None:
                self._on_notify(info, data)

            await client.start_notify(async_char, on_notify)
            tx_char = client.services.get_characteristic(SYNC_TX_CHAR_UUID)
            if tx_char is not None and {"notify", "indicate"} & set(tx_char.properties):
                try:
                    await client.start_notify(tx_char, on_notify)
                except BleakError as exc:
                    logger.debug("%s: sync TX subscribe failed: %s", info.label, exc)

            await client.write_gatt_char(rx_char, RIDE_ON, response=False)
            if info.device_type == DeviceType.RIDE_LEFT and is_ride_v2_firmware(info.firmware):
                await client.write_gatt_char(rx_char, UNLOCKED_START, response=False)

            self._set(info, "connected", "")
            logger.info("Connected %s (firmware %s)", info.label, info.firmware or "?")
            await disconnected.wait()
            self._set(info, "asleep", "disconnected — press any button on it to wake it")
            logger.info("%s disconnected", info.label)
            self._rescan.set()
        except asyncio.CancelledError:
            self._set(info, "seen", "reconnecting…")
            raise
        except Exception as exc:  # BleakError, TimeoutError, CoreBluetooth errors...
            message = str(exc) or type(exc).__name__
            self._retry_at[address] = time.monotonic() + RETRY_DELAY
            self._set(info, "error", message)
            logger.warning("%s: connection failed: %s", info.label, message)
        finally:
            self._clients.pop(address, None)
            self._tasks.pop(address, None)
            self.on_disconnect(address)
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _read_bytes(self, client: BleakClient, uuid: str) -> bytes | None:
        char = client.services.get_characteristic(uuid)
        if char is None or "read" not in char.properties:
            return None
        try:
            return bytes(await client.read_gatt_char(char))
        except Exception as exc:
            logger.debug("read %s failed: %s", uuid, exc)
            return None

    async def _read_text(self, client: BleakClient, uuid: str) -> str | None:
        data = await self._read_bytes(client, uuid)
        return data.decode("utf-8", errors="replace").strip("\x00 ") if data else None

    def _on_notify(self, info: DeviceInfo, data: bytearray) -> None:
        if self.raw_debug:
            logger.info("%s ← %s", info.label, bytes(data).hex(" "))
        try:
            message = parse_message(bytes(data), self.paddle_threshold())
        except DecodeError as exc:
            logger.debug("%s: undecodable message %s (%s)", info.label, bytes(data).hex(" "), exc)
            return
        if message.keypad is not None:
            self.on_keypad(info.address, message.keypad)
        elif message.battery is not None and message.battery != info.battery:
            info.battery = message.battery
            self.on_device(info)

    async def disconnect_all(self) -> None:
        self._retry_at.clear()
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._rescan.set()

    async def stop(self) -> None:
        self._stop.set()
        await self.disconnect_all()
