"""Experimental MyWhoosh Link output.

MyWhoosh can be driven by its "MyWhoosh Link" companion app. The game connects
*out* to the Link device on TCP port 21587 and then accepts newline-delimited
JSON such as::

    {"MessageType": "Controls", "InGameControls": {"GearShifting": "1"}}

Running this server lets MyWhoosh shift, steer, change camera, U-turn and tuck
without having keyboard focus. MyWhoosh only connects after the official Link
app has been used once from this Mac's network address, so this is opt-in and
falls back to the keyboard whenever no game is connected.

Wire format as documented by BikeControl's MyWhoosh Link support.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

logger = logging.getLogger(__name__)

LINK_PORT = 21587


class LinkServer:
    def __init__(self, on_change: Callable[[], None] | None = None, port: int = LINK_PORT, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.on_change = on_change or (lambda: None)
        self._server: asyncio.base_events.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.peer: str | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def start(self) -> None:
        if self._server:
            return
        self.error = None
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port, reuse_address=True)
        except OSError as exc:
            self.error = f"can't listen on port {self.port}: {exc.strerror or exc}"
            logger.warning("MyWhoosh Link: %s", self.error)
            self.on_change()
            return
        logger.info("MyWhoosh Link: waiting for MyWhoosh on port %d", self.port)
        self.on_change()

    async def stop(self) -> None:
        if self._writer:
            self._writer.close()
            self._writer = None
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.peer = None
        self.on_change()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._writer and not self._writer.is_closing():
            self._writer.close()  # newest connection wins
        self._writer = writer
        host, port, *_ = writer.get_extra_info("peername") or ("?", 0)
        self.peer = f"{host}:{port}"
        logger.info("MyWhoosh Link: MyWhoosh connected from %s", self.peer)
        self.on_change()
        try:
            while line := await reader.readline():
                logger.debug("MyWhoosh Link received: %s", line.decode(errors="replace").strip())
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
                self.peer = None
                logger.info("MyWhoosh Link: MyWhoosh disconnected")
                self.on_change()
            writer.close()

    def send_controls(self, controls: dict[str, str]) -> bool:
        if not self.connected:
            return False
        message = json.dumps({"MessageType": "Controls", "InGameControls": controls})
        assert self._writer is not None
        self._writer.write(message.encode() + b"\n")
        return True
