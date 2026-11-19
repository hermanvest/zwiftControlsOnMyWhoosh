"""Glue between controllers and MyWhoosh.

``ButtonHub`` merges button state from every connected controller (so a button
reported by two devices only fires once), ``Bridge`` turns presses into
MyWhoosh actions and delivers them over MyWhoosh Link or the keyboard.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from zwiftcontrols.actions import Action
from zwiftcontrols.ble import DeviceInfo, DeviceManager
from zwiftcontrols.config import Config
from zwiftcontrols.link import LinkServer
from zwiftcontrols.macos import KeySender, key_label
from zwiftcontrols.protocol import Button, KeypadState

logger = logging.getLogger(__name__)

MAX_QUEUED = 12  # drop auto-repeats beyond this so a stuck focus can't build a backlog


class ButtonHub:
    def __init__(self, on_press: Callable[[Button], None], on_release: Callable[[Button], None]):
        self.on_press = on_press
        self.on_release = on_release
        self._device_state: dict[str, frozenset[Button]] = {}
        self._holders: dict[Button, set[str]] = {}

    @property
    def pressed(self) -> frozenset[Button]:
        return frozenset(self._holders)

    def update(self, device_id: str, pressed: frozenset[Button]) -> None:
        before = self._device_state.get(device_id, frozenset())
        self._device_state[device_id] = pressed
        for button in sorted(pressed - before):
            holders = self._holders.setdefault(button, set())
            first = not holders
            holders.add(device_id)
            if first:
                self.on_press(button)
        for button in sorted(before - pressed):
            holders = self._holders.get(button)
            if holders is None:
                continue
            holders.discard(device_id)
            if not holders:
                del self._holders[button]
                self.on_release(button)

    def drop(self, device_id: str) -> None:
        """Release everything a (disconnected) device was holding."""
        self.update(device_id, frozenset())
        self._device_state.pop(device_id, None)


@dataclass(frozen=True)
class ActionEvent:
    button: Button
    action: Action
    phase: str  # tap | down | up
    route: str  # "key K", "Link", ...
    sent: bool
    note: str = ""


class Bridge:
    def __init__(
        self,
        config: Config,
        *,
        on_device: Callable[[DeviceInfo], None] = lambda info: None,
        on_button: Callable[[Button, bool], None] = lambda button, pressed: None,
        on_action: Callable[[ActionEvent], None] = lambda event: None,
        on_status: Callable[[], None] = lambda: None,
        raw_debug: bool = False,
        key_sender: KeySender | None = None,
    ):
        self.config = config
        self.on_button = on_button
        self.on_action = on_action
        self.output_enabled = True
        self.hub = ButtonHub(self._press, self._release)
        self.keys = key_sender or KeySender(config.delivery)
        self.link = LinkServer(on_change=on_status)
        self.devices = DeviceManager(
            on_keypad=self.handle_keypad,
            on_device=on_device,
            on_disconnect=self.hub.drop,
            paddle_threshold=lambda: config.paddle_threshold,
            raw_debug=raw_debug,
        )
        self._queue: asyncio.Queue[tuple[Button, Action, str]] = asyncio.Queue()
        self._held: dict[Button, Action] = {}
        self._down_route: dict[Button, str] = {}
        self._repeaters: dict[Button, asyncio.Task] = {}
        self._worker: asyncio.Task | None = None
        self._camera = 0

    # --- lifecycle ---

    def start_worker(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._drain())

    async def run(self) -> None:
        self.start_worker()
        if self.config.link_enabled:
            await self.link.start()
        await self.devices.run()

    async def stop(self) -> None:
        for button in list(self._held):
            self._release(button)
        await self.devices.stop()
        await self.link.stop()
        await asyncio.sleep(0.05)  # let pending key-ups drain
        if self._worker:
            self._worker.cancel()

    # --- settings ---

    def set_delivery(self, mode: str) -> None:
        self.config.delivery = mode
        self.keys.delivery = mode

    async def set_link_enabled(self, enabled: bool) -> None:
        self.config.link_enabled = enabled
        if enabled:
            await self.link.start()
        else:
            await self.link.stop()

    # --- input ---

    def handle_keypad(self, device_id: str, state: KeypadState) -> None:
        self.hub.update(device_id, state.pressed)

    def _press(self, button: Button) -> None:
        self.on_button(button, True)
        action = self.config.action_for(button)
        if action is None or not self.output_enabled:
            return
        self._held[button] = action
        self._enqueue(button, action, "down" if action.hold else "tap")
        if action.repeat:
            self._repeaters[button] = asyncio.create_task(self._repeat(button, action))

    def _release(self, button: Button) -> None:
        self.on_button(button, False)
        repeater = self._repeaters.pop(button, None)
        if repeater:
            repeater.cancel()
        action = self._held.pop(button, None)
        if action is not None and action.hold:
            self._enqueue(button, action, "up")

    def _enqueue(self, button: Button, action: Action, phase: str, *, droppable: bool = False) -> None:
        if droppable and self._queue.qsize() >= MAX_QUEUED:
            return
        self._queue.put_nowait((button, action, phase))

    async def _repeat(self, button: Button, action: Action) -> None:
        await asyncio.sleep(self.config.repeat_delay)
        while self._held.get(button) is action:
            self._enqueue(button, action, "tap", droppable=True)
            await asyncio.sleep(self.config.repeat_interval)

    # --- output ---

    async def _drain(self) -> None:
        while True:
            button, action, phase = await self._queue.get()
            try:
                route, sent, note = await self._perform(button, action, phase)
            except Exception as exc:  # never let one bad event kill the output loop
                logger.exception("Sending %s failed", action.id)
                route, sent, note = "error", False, str(exc)
            if phase != "up" or not sent:
                self.on_action(ActionEvent(button, action, phase, route, sent, note))

    async def _perform(self, button: Button, action: Action, phase: str) -> tuple[str, bool, str]:
        if phase == "up":
            route = self._down_route.pop(button, "key")
        elif self.link.connected and action.link:
            route = "link"
        else:
            route = "key"
        if phase == "down":
            self._down_route[button] = route

        if route == "link":
            controls = action.link_release if phase == "up" else action.link
            if controls:
                controls = dict(controls)
                if controls.get("CameraAngle") == "cycle":
                    self._camera = self._camera % max(1, self.config.camera_views) + 1
                    controls["CameraAngle"] = str(self._camera)
                if not self.link.send_controls(controls):
                    return "Link", False, "MyWhoosh Link disconnected"
            return "Link", True, ""

        if action.link_only:
            return "Link", False, "needs MyWhoosh Link (press l)"
        key = self.config.key_for(action)
        if not key:
            return "key", False, "no key assigned"
        sent, note = await self.keys.send(key, phase)
        return f"key {key_label(key)}", sent, note
