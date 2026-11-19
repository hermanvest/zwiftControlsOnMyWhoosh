"""User settings, stored as JSON in ~/.config/zwhoosh/config.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from zwiftcontrols.actions import ACTIONS_BY_ID, DEFAULT_MAPPING, Action
from zwiftcontrols.protocol import DEFAULT_PADDLE_THRESHOLD, Button

logger = logging.getLogger(__name__)

DELIVERY_MODES = ("focus", "background", "frontmost")
DELIVERY_LABELS = {
    "focus": "keyboard → MyWhoosh (auto-focus)",
    "background": "keyboard → MyWhoosh in background (experimental)",
    "frontmost": "keyboard → whatever app is in front",
}


def default_config_path() -> Path:
    override = os.environ.get("ZWHOOSH_CONFIG")
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "zwhoosh" / "config.json"


@dataclass
class Config:
    mapping: dict[Button, str] = field(default_factory=lambda: dict(DEFAULT_MAPPING))
    key_overrides: dict[str, str] = field(default_factory=dict)  # action id -> key name
    delivery: str = "focus"
    swap_shift_keys: bool = False
    link_enabled: bool = False
    paddle_threshold: int = DEFAULT_PADDLE_THRESHOLD
    repeat_delay: float = 0.45
    repeat_interval: float = 0.18
    camera_views: int = 6
    path: Path | None = field(default=None, compare=False, repr=False)

    def action_for(self, button: Button) -> Action | None:
        action = ACTIONS_BY_ID.get(self.mapping.get(button, "none"))
        if action is None or action.id == "none":
            return None
        return action

    def key_for(self, action: Action) -> str | None:
        if action.id in self.key_overrides:
            return self.key_overrides[action.id] or None
        if self.swap_shift_keys and action.id in ("gear_harder", "gear_easier"):
            other = "gear_easier" if action.id == "gear_harder" else "gear_harder"
            return ACTIONS_BY_ID[other].key
        return action.key

    def reset_mapping(self) -> None:
        self.mapping = dict(DEFAULT_MAPPING)

    # --- persistence ---

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        config = cls(path=path)
        if not path.exists():
            return config
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable config %s: %s", path, exc)
            return config

        for name, action_id in (raw.get("mapping") or {}).items():
            try:
                button = Button(name)
            except ValueError:
                logger.warning("Config: unknown button %r ignored", name)
                continue
            if action_id not in ACTIONS_BY_ID:
                logger.warning("Config: unknown action %r for %s ignored", action_id, name)
                continue
            config.mapping[button] = action_id

        from zwiftcontrols.macos import KEYCODES  # local import: keeps config importable in tests

        for action_id, key in (raw.get("keys") or {}).items():
            if action_id not in ACTIONS_BY_ID:
                logger.warning("Config: unknown action %r in keys ignored", action_id)
            elif key and key.lower() not in KEYCODES:
                logger.warning("Config: unknown key %r for %s ignored", key, action_id)
            else:
                config.key_overrides[action_id] = (key or "").lower()

        if raw.get("delivery") in DELIVERY_MODES:
            config.delivery = raw["delivery"]
        for name in ("swap_shift_keys", "link_enabled"):
            if isinstance(raw.get(name), bool):
                setattr(config, name, raw[name])
        for name, kind in (("paddle_threshold", int), ("repeat_delay", float), ("repeat_interval", float), ("camera_views", int)):
            value = raw.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                setattr(config, name, kind(value))
        return config

    def to_json(self) -> dict:
        return {
            "mapping": {button.value: action_id for button, action_id in self.mapping.items()},
            "keys": dict(self.key_overrides),
            "delivery": self.delivery,
            "swap_shift_keys": self.swap_shift_keys,
            "link_enabled": self.link_enabled,
            "paddle_threshold": self.paddle_threshold,
            "repeat_delay": self.repeat_delay,
            "repeat_interval": self.repeat_interval,
            "camera_views": self.camera_views,
        }

    def save(self) -> None:
        path = self.path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2) + "\n")
        tmp.replace(path)
