"""MyWhoosh actions and the default Zwift Ride button layout.

Keyboard shortcuts (MyWhoosh on macOS):

* ``I`` / ``K`` - virtual shifting (official). ``K`` = harder gear, ``I`` =
  easier gear (direction per in-game testing by gearpad.app and BikeControl's
  MyWhoosh keymap; press ``x`` in the UI to swap if your install differs).
* ``A`` / ``D`` and arrow keys - steering while held; arrows also move around
  menus.
* ``Return`` / ``Escape`` - select / back and pause menu.
* ``H`` hide controls, ``U`` minimal UI, ``1``-``7`` emotes.

Camera, U-turn and tuck have no keyboard shortcut; they only work over the
experimental MyWhoosh Link connection (see link.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from zwiftcontrols.protocol import Button


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    key: str | None = None  # key name from macos.KEYCODES
    hold: bool = False  # key stays down while the button is held (steering)
    repeat: bool = False  # re-fires while the button is held (shifting, menus)
    link: dict[str, str] | None = None  # MyWhoosh Link "InGameControls" on press
    link_release: dict[str, str] | None = None  # ... and on release (hold actions)
    link_only: bool = False


ACTIONS: list[Action] = [
    Action("none", "— nothing —"),
    Action("gear_harder", "Gear harder", "k", repeat=True, link={"GearShifting": "1"}),
    Action("gear_easier", "Gear easier", "i", repeat=True, link={"GearShifting": "-1"}),
    Action("nav_up", "Menu up", "up", repeat=True),
    Action("nav_down", "Menu down", "down", repeat=True),
    Action("nav_left", "Menu left / steer left", "left", hold=True),
    Action("nav_right", "Menu right / steer right", "right", hold=True),
    Action("select", "Select / OK", "return"),
    Action("back", "Back", "escape"),
    Action("pause", "Pause / menu", "escape"),
    Action("steer_left", "Steer left", "a", hold=True, link={"Steering": "-1"}, link_release={"Steering": "0"}),
    Action("steer_right", "Steer right", "d", hold=True, link={"Steering": "1"}, link_release={"Steering": "0"}),
    Action("hide_hud", "Hide controls", "h"),
    Action("minimal_ui", "Minimal UI", "u"),
    Action("emote_1", "Emote: peace", "1"),
    Action("emote_2", "Emote: wave", "2"),
    Action("emote_3", "Emote: fist bump", "3"),
    Action("emote_4", "Emote: dab", "4"),
    Action("emote_5", "Emote: elbow flick", "5"),
    Action("emote_6", "Emote: toast", "6"),
    Action("emote_7", "Emote: thumbs up", "7"),
    Action("camera", "Next camera view (Link)", link={"CameraAngle": "cycle"}, link_only=True),
    Action("uturn", "U-turn (Link)", link={"UTurn": "true"}, link_only=True),
    Action("tuck", "Tuck (Link)", link={"Tuck": "true"}, link_only=True),
]

ACTIONS_BY_ID: dict[str, Action] = {action.id: action for action in ACTIONS}

BUTTON_LABELS: dict[Button, str] = {
    Button.SHIFT_UP_R: "Right shift button (upper) / Click +",
    Button.SHIFT_DN_R: "Right shift button (lower)",
    Button.PADDLE_R: "Right paddle",
    Button.POWERUP_R: "Right drop button",
    Button.SHIFT_UP_L: "Left shift button (upper) / Click −",
    Button.SHIFT_DN_L: "Left shift button (lower)",
    Button.PADDLE_L: "Left paddle",
    Button.POWERUP_L: "Left drop button",
    Button.UP: "D-pad ▲",
    Button.DOWN: "D-pad ▼",
    Button.LEFT: "D-pad ◀",
    Button.RIGHT: "D-pad ▶",
    Button.A: "A",
    Button.B: "B",
    Button.Y: "Y",
    Button.Z: "Z",
    Button.ONOFF_L: "Left power button",
    Button.ONOFF_R: "Right power button",
}

# Display order: BUTTON_LABELS' order.
BUTTON_ORDER: list[Button] = list(BUTTON_LABELS)

DEFAULT_MAPPING: dict[Button, str] = {
    # Right hood shifts harder, left hood easier - like Zwift.
    Button.SHIFT_UP_R: "gear_harder",
    Button.SHIFT_DN_R: "gear_harder",
    Button.PADDLE_R: "gear_harder",
    Button.POWERUP_R: "gear_harder",
    Button.SHIFT_UP_L: "gear_easier",
    Button.SHIFT_DN_L: "gear_easier",
    Button.PADDLE_L: "gear_easier",
    Button.POWERUP_L: "gear_easier",
    # Left hood D-pad: menus, and steering when held during a ride.
    Button.UP: "nav_up",
    Button.DOWN: "nav_down",
    Button.LEFT: "nav_left",
    Button.RIGHT: "nav_right",
    # Right hood face buttons.
    Button.A: "select",
    Button.B: "back",
    Button.Y: "pause",
    Button.Z: "emote_7",
    Button.ONOFF_L: "hide_hud",
    Button.ONOFF_R: "minimal_ui",
}
