"""Command line entry point: ``zwhoosh`` (terminal UI) or ``zwhoosh --headless``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from zwiftcontrols import __version__
from zwiftcontrols.config import DELIVERY_MODES, Config, default_config_path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="zwhoosh",
        description="Use a Zwift Ride (or Zwift Click) as a controller for MyWhoosh on macOS.",
    )
    parser.add_argument("--headless", action="store_true", help="no UI, just log to the terminal")
    parser.add_argument("--debug", action="store_true", help="log raw Bluetooth messages")
    parser.add_argument("--config", type=Path, help=f"settings file (default: {default_config_path()})")
    parser.add_argument("--delivery", choices=DELIVERY_MODES, help="how key presses reach MyWhoosh (saved)")
    parser.add_argument("--link", action=argparse.BooleanOptionalAction, help="enable the experimental MyWhoosh Link server (saved)")
    parser.add_argument("--version", action="version", version=f"zwhoosh {__version__}")
    return parser.parse_args(argv)


async def _run_headless(config: Config, raw_debug: bool) -> None:
    from zwiftcontrols.actions import BUTTON_LABELS
    from zwiftcontrols.engine import ActionEvent, Bridge
    from zwiftcontrols.macos import post_access_granted, request_post_access

    log = logging.getLogger("zwhoosh")

    def on_action(event: ActionEvent) -> None:
        label = BUTTON_LABELS[event.button]
        if event.sent:
            log.info("%s → %s · %s%s", label, event.action.label, event.route, f" ({event.note})" if event.note else "")
        else:
            log.warning("%s → %s · not sent: %s", label, event.action.label, event.note)

    def on_device(info) -> None:
        log.info("%s: %s%s", info.label, info.status, f" — {info.detail}" if info.detail else "")

    if not post_access_granted():
        request_post_access()
        log.warning("Keyboard output blocked: allow your terminal in System Settings → Privacy & Security → Accessibility")

    bridge = Bridge(config, on_action=on_action, on_device=on_device, raw_debug=raw_debug)
    log.info("Scanning for Zwift controllers — press a button on your Ride to wake it. Ctrl-C to quit.")
    try:
        await bridge.run()
    finally:
        await bridge.stop()


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "darwin":
        print("zwhoosh only runs on macOS.", file=sys.stderr)
        return 1
    args = _parse_args(argv)
    config = Config.load(args.config)
    changed = False
    if args.delivery:
        config.delivery, changed = args.delivery, True
    if args.link is not None:
        config.link_enabled, changed = args.link, True
    if changed:
        config.save()

    if args.headless:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
        try:
            asyncio.run(_run_headless(config, args.debug))
        except KeyboardInterrupt:
            pass
        return 0

    from zwiftcontrols.tui import ZwhooshApp

    ZwhooshApp(config, raw_debug=args.debug).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
