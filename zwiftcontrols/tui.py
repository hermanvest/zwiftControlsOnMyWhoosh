"""Terminal UI: connection status, live button map and remapping."""

from __future__ import annotations

import logging
import threading
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from zwiftcontrols import __version__
from zwiftcontrols.actions import ACTIONS, ACTIONS_BY_ID, BUTTON_LABELS, BUTTON_ORDER
from zwiftcontrols.ble import DeviceInfo
from zwiftcontrols.config import DELIVERY_LABELS, DELIVERY_MODES, Config
from zwiftcontrols.engine import ActionEvent, Bridge
from zwiftcontrols.macos import (
    bluetooth_authorization,
    key_label,
    mywhoosh_state,
    open_privacy_settings,
    post_access_granted,
    request_post_access,
)
from zwiftcontrols.protocol import Button

STATUS_STYLES = {
    "connected": "bold green",
    "connecting": "yellow",
    "seen": "yellow",
    "locked": "bold red",
    "error": "red",
    "asleep": "dim",
    "skipped": "dim",
}


class _UILogHandler(logging.Handler):
    def __init__(self, app: "ZwhooshApp"):
        super().__init__(logging.INFO)
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        style = "red" if record.levelno >= logging.ERROR else "yellow" if record.levelno >= logging.WARNING else ""
        if threading.current_thread() is threading.main_thread():
            self.app.write_log(message, style)
        else:
            self.app.call_from_thread(self.app.write_log, message, style)


class ActionPicker(ModalScreen[str | None]):
    """Pick the action a button triggers."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    ActionPicker { align: center middle; }
    #picker { width: 64; height: auto; max-height: 90%; border: thick $accent; background: $surface; padding: 0 1; }
    #picker OptionList { height: auto; max-height: 30; }
    """

    def __init__(self, button: Button, config: Config):
        super().__init__()
        self.button = button
        self.config = config

    def compose(self) -> ComposeResult:
        options = []
        for action in ACTIONS:
            if action.link_only:
                hint = "Link only"
            elif action.key:
                hint = key_label(self.config.key_for(action))
            else:
                hint = ""
            options.append(Option(Text.assemble(f"{action.label:<30}", (hint, "dim")), id=action.id))
        with Vertical(id="picker"):
            yield Static(f"Map [b]{BUTTON_LABELS[self.button]}[/b] to:  [dim](Enter to pick, Esc to cancel)[/dim]")
            yield OptionList(*options)

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        current = self.config.mapping.get(self.button, "none")
        ids = [action.id for action in ACTIONS]
        option_list.highlighted = ids.index(current) if current in ids else 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ZwhooshApp(App):
    TITLE = "zwhoosh"
    SUB_TITLE = "Zwift Ride → MyWhoosh"
    CSS = """
    #top { height: auto; }
    #devices-box { width: 3fr; height: auto; border: round $accent; }
    #devices { height: auto; max-height: 7; }
    #hint { padding: 0 1; color: $text-muted; }
    #status { width: 2fr; height: auto; min-height: 9; border: round $accent; padding: 0 1; }
    #mapping { height: 1fr; border: round $accent; }
    #log { height: 9; border: round $accent; }
    """
    BINDINGS = [
        Binding("o", "toggle_output", "Output on/off"),
        Binding("m", "cycle_delivery", "Delivery"),
        Binding("x", "swap_shift", "Swap I/K"),
        Binding("l", "toggle_link", "Link"),
        Binding("c", "reconnect", "Reconnect"),
        Binding("r", "reset_mapping", "Reset map"),
        Binding("p", "permissions", "Permissions"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self, config: Config, *, raw_debug: bool = False, bridge: Bridge | None = None, start_bridge: bool = True):
        super().__init__()
        self.config = config
        self.raw_debug = raw_debug
        self.start_bridge = start_bridge
        self.bridge = bridge or Bridge(
            config,
            on_device=self.update_device,
            on_button=self.show_button,
            on_action=self.show_action,
            on_status=self._on_bridge_status,
            raw_debug=raw_debug,
        )
        self._log_handler = _UILogHandler(self)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        self._pressed: set[Button] = set()

    # --- layout ---

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="top"):
            with Vertical(id="devices-box"):
                yield DataTable(id="devices", cursor_type="none", zebra_stripes=False)
                yield Static(id="hint")
            yield Static(id="status")
        yield DataTable(id="mapping", cursor_type="row")
        yield RichLog(id="log", markup=False, wrap=True, max_lines=500)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#devices-box").border_title = "Controllers"
        self.query_one("#status").border_title = "Status"
        mapping = self.query_one("#mapping", DataTable)
        mapping.border_title = "Buttons — press one to see it light up · Enter/click a row to remap"
        self.query_one("#log", RichLog).border_title = "Log"

        devices = self.query_one("#devices", DataTable)
        for label, key in (("Controller", "name"), ("Status", "status"), ("Battery", "battery"), ("Firmware", "firmware"), ("Signal", "rssi")):
            devices.add_column(label, key=key)

        mapping.add_column(" ", key="live", width=2)
        mapping.add_column("Button", key="button", width=38)
        mapping.add_column("Action", key="action", width=28)
        mapping.add_column("Key", key="key", width=12)
        for button in BUTTON_ORDER:
            mapping.add_row(*self._mapping_cells(button), key=button.value)
        mapping.focus()

        root = logging.getLogger()
        root.addHandler(self._log_handler)
        root.setLevel(logging.INFO)

        self.write_log(f"zwhoosh {__version__} — scanning for Zwift controllers. Press a button on your Ride to wake it.")
        if not post_access_granted():
            request_post_access()
            self.write_log(
                "Keyboard output is blocked: allow your terminal app in System Settings → Privacy & Security → "
                "Accessibility (press p to open it), then restart zwhoosh.",
                "yellow",
            )
        self.refresh_status()
        self.refresh_hint()
        self.set_interval(1.0, self.refresh_status)
        if self.start_bridge:
            self.run_worker(self.bridge.run(), name="bridge", exit_on_error=False)

    def on_unmount(self) -> None:
        logging.getLogger().removeHandler(self._log_handler)

    # --- rendering helpers ---

    def write_log(self, message: str, style: str = "") -> None:
        try:
            log = self.query_one("#log", RichLog)
        except Exception:
            return
        log.write(Text.assemble((time.strftime("%H:%M:%S "), "dim"), (message, style)))

    def _key_cell(self, button: Button) -> Text:
        action = self.config.action_for(button)
        if action is None:
            return Text("—", "dim")
        if action.link_only:
            return Text("Link", "cyan" if self.bridge.link.connected else "dim")
        label = key_label(self.config.key_for(action))
        if action.link and self.bridge.link.connected:
            return Text("Link", "cyan")
        return Text(label)

    def _mapping_cells(self, button: Button) -> tuple:
        pressed = button in self._pressed
        action = self.config.action_for(button)
        style = "bold reverse green" if pressed else ""
        return (
            Text("●", "bold green") if pressed else Text(""),
            Text(BUTTON_LABELS[button], style),
            Text(action.label if action else "— nothing —", style if action else "dim"),
            self._key_cell(button),
        )

    def refresh_mapping_row(self, button: Button) -> None:
        table = self.query_one("#mapping", DataTable)
        for column, value in zip(("live", "button", "action", "key"), self._mapping_cells(button), strict=True):
            table.update_cell(button.value, column, value)

    def refresh_all_mapping(self) -> None:
        for button in BUTTON_ORDER:
            self.refresh_mapping_row(button)

    def refresh_status(self) -> None:
        try:
            status = self.query_one("#status", Static)
        except Exception:
            return
        running, front = mywhoosh_state()
        if front:
            whoosh = "[green]● running, in front[/]"
        elif running:
            whoosh = "[yellow]● running, in background[/]"
        else:
            whoosh = "[red]○ not running[/]"
        keys = "[green]✓ allowed[/]" if post_access_granted() else "[red]✗ blocked[/] (press p)"
        bt = bluetooth_authorization()
        bt_text = "[green]✓ allowed[/]" if bt == "allowed" else f"[red]✗ {bt}[/] (press p)"
        if self.bridge.devices.scan_error:
            bt_text += " [red]scan error[/]"
        output = "[green]ON[/]" if self.bridge.output_enabled else "[yellow]PAUSED[/] (o)"
        link = self.bridge.link
        if link.connected:
            link_text = f"[cyan]connected[/] {link.peer}"
        elif link.error:
            link_text = f"[red]{link.error}[/]"
        elif link.running:
            link_text = f"waiting for MyWhoosh on :{link.port}"
        else:
            link_text = "[dim]off (l to enable)[/]"
        harder = key_label(self.config.key_for(ACTIONS_BY_ID["gear_harder"]))
        easier = key_label(self.config.key_for(ACTIONS_BY_ID["gear_easier"]))
        rows = [
            ("MyWhoosh", whoosh),
            ("Keyboard", keys),
            ("Bluetooth", bt_text),
            ("Output", output),
            ("Delivery", DELIVERY_LABELS[self.config.delivery]),
            ("Shifting", f"harder [b]{harder}[/b] · easier [b]{easier}[/b] (x swaps)"),
            ("Link", link_text),
        ]
        status.update("\n".join(f"[b]{name:<10}[/b]{value}" for name, value in rows))
        self.refresh_hint()

    def refresh_hint(self) -> None:
        try:
            hint = self.query_one("#hint", Static)
        except Exception:
            return
        devices = list(self.bridge.devices.devices.values())
        problem = next((d for d in devices if d.status in ("locked", "error") and d.detail), None)
        if self.bridge.devices.scan_error:
            text = f"[red]Bluetooth: {self.bridge.devices.scan_error}[/]"
        elif problem is not None:
            text = f"[red]{problem.label}:[/] {problem.detail}"
        elif not any(d.status == "connected" for d in devices):
            text = "Scanning… press any button on your Zwift Ride (or Click) to wake it."
        else:
            text = "Ready. Ride buttons now drive MyWhoosh."
        hint.update(text)

    # --- bridge callbacks ---

    def _on_bridge_status(self) -> None:
        # Link connected/disconnected: the Key column shows which route is used.
        if self.is_mounted:
            self.refresh_status()
            self.refresh_all_mapping()

    def update_device(self, info: DeviceInfo) -> None:
        table = self.query_one("#devices", DataTable)
        cells = (
            Text(info.label),
            Text(info.status, STATUS_STYLES.get(info.status, "")),
            Text(f"{info.battery}%" if info.battery is not None else "—"),
            Text(info.firmware or "—"),
            Text(f"{info.rssi} dBm" if info.rssi is not None else "—"),
        )
        if info.address in table.rows:
            for column, value in zip(("name", "status", "battery", "firmware", "rssi"), cells, strict=True):
                table.update_cell(info.address, column, value)
        else:
            table.add_row(*cells, key=info.address)
        self.refresh_hint()

    def show_button(self, button: Button, pressed: bool) -> None:
        if pressed:
            self._pressed.add(button)
        else:
            self._pressed.discard(button)
        self.refresh_mapping_row(button)
        if pressed and self.config.action_for(button) is None:
            self.write_log(f"{BUTTON_LABELS[button]} (not mapped)", "dim")

    def show_action(self, event: ActionEvent) -> None:
        label = BUTTON_LABELS[event.button]
        if event.sent:
            extra = f" ({event.note})" if event.note else ""
            self.write_log(f"{label} → {event.action.label} · {event.route}{extra}", "green")
        else:
            self.write_log(f"{label} → {event.action.label} · not sent: {event.note}", "yellow")

    # --- actions ---

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "mapping" or event.row_key.value is None:
            return
        button = Button(event.row_key.value)

        def apply(action_id: str | None) -> None:
            if action_id is None:
                return
            self.config.mapping[button] = action_id
            self.config.save()
            self.refresh_mapping_row(button)
            self.write_log(f"{BUTTON_LABELS[button]} now → {ACTIONS_BY_ID[action_id].label} (saved)")

        self.push_screen(ActionPicker(button, self.config), apply)

    def action_toggle_output(self) -> None:
        self.bridge.output_enabled = not self.bridge.output_enabled
        self.write_log("Output " + ("ON — buttons drive MyWhoosh" if self.bridge.output_enabled else "PAUSED — buttons only light up here"))
        self.refresh_status()

    def action_cycle_delivery(self) -> None:
        index = DELIVERY_MODES.index(self.config.delivery)
        self.bridge.set_delivery(DELIVERY_MODES[(index + 1) % len(DELIVERY_MODES)])
        self.config.save()
        self.write_log(f"Delivery: {DELIVERY_LABELS[self.config.delivery]}")
        self.refresh_status()

    def action_swap_shift(self) -> None:
        self.config.swap_shift_keys = not self.config.swap_shift_keys
        self.config.save()
        self.refresh_all_mapping()
        self.refresh_status()
        harder = key_label(self.config.key_for(ACTIONS_BY_ID["gear_harder"]))
        self.write_log(f"Shift keys swapped: harder gear is now {harder}")

    async def action_toggle_link(self) -> None:
        await self.bridge.set_link_enabled(not self.config.link_enabled)
        self.config.save()
        self.refresh_status()
        self.refresh_all_mapping()

    async def action_reconnect(self) -> None:
        self.write_log("Reconnecting all controllers…")
        await self.bridge.devices.disconnect_all()

    def action_reset_mapping(self) -> None:
        self.config.reset_mapping()
        self.config.save()
        self.refresh_all_mapping()
        self.write_log("Button mapping reset to defaults")

    def action_permissions(self) -> None:
        if not post_access_granted():
            request_post_access()
            open_privacy_settings("Accessibility")
            self.write_log("Enable your terminal app under Accessibility, then restart zwhoosh.", "yellow")
        elif bluetooth_authorization() != "allowed":
            open_privacy_settings("Bluetooth")
            self.write_log("Enable your terminal app under Bluetooth, then restart zwhoosh.", "yellow")
        else:
            self.write_log("Keyboard and Bluetooth permissions are already granted.")

    async def action_quit(self) -> None:
        self.write_log("Disconnecting…")
        try:
            await self.bridge.stop()
        finally:
            self.exit()
