# zwhoosh — Zwift Ride controls for MyWhoosh (macOS)

Use the **Zwift Ride** smart frame's controllers (or a Zwift Click / Click v2)
as a full controller for **MyWhoosh** on a Mac: virtual shifting, steering,
menu navigation, select/back, pause, HUD and emotes. A terminal UI shows which
controllers are connected and lights up each button as you press it, and you can
remap any button from there.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/hermanvest/zwiftControlsOnMyWhoosh/main/install.sh | bash
```

Or with `wget`, if you have it (`brew install wget`):

```sh
wget -qO- https://raw.githubusercontent.com/hermanvest/zwiftControlsOnMyWhoosh/main/install.sh | bash
```

This installs [uv](https://docs.astral.sh/uv/) if you don't have it, then puts a
`zwhoosh` command in `~/.local/bin`. Re-run it to update. Uninstall with
`uv tool uninstall zwhoosh`.

## Run

```sh
zwhoosh
```

1. Press any button on the Ride to wake it (LEDs blink). zwhoosh finds and
   connects it automatically, and reconnects when it wakes up after sleeping.
2. Start MyWhoosh and ride. Button presses are sent to MyWhoosh, and if
   another app is in front, MyWhoosh is brought forward first. Nothing is sent
   while MyWhoosh isn't running.

**First run:** macOS asks for two permissions for your terminal app (Terminal,
iTerm, …):

- **Bluetooth** to talk to the controllers.
- **Accessibility** to press MyWhoosh's keys: System Settings → Privacy &
  Security → Accessibility → enable your terminal. Restart zwhoosh afterwards.
  Press `p` in zwhoosh to open the right settings page.

### Keys in the UI

| Key | |
|---|---|
| `Enter` / click a row | remap that button |
| `o` | output on/off (pause to test buttons without touching MyWhoosh) |
| `x` | swap shift direction (I ↔ K) |
| `m` | delivery mode (see below) |
| `l` | MyWhoosh Link on/off (experimental) |
| `c` | reconnect controllers |
| `r` | reset button mapping |
| `p` | open permission settings |
| `q` | quit |

`zwhoosh --headless` runs without the UI (plain log). `zwhoosh --debug` logs
raw Bluetooth messages.

## Default layout

| Zwift Ride button | MyWhoosh action | Key |
|---|---|---|
| Right shift buttons, right paddle, right drop button | gear harder | `K` |
| Left shift buttons, left paddle, left drop button | gear easier | `I` |
| D-pad ▲ ▼ | menu up / down | `↑` `↓` |
| D-pad ◀ ▶ (hold to steer) | menu left / right, steer | `←` `→` |
| A | select | `Return` |
| B | back | `Esc` |
| Y | pause / menu | `Esc` |
| Z | emote: thumbs up | `7` |
| Left power button | hide controls | `H` |
| Right power button | minimal UI | `U` |

Holding a shift button or D-pad ▲/▼ repeats it. On a Zwift Click, `+` is
"gear harder" and `−` is "gear easier".

Other actions available for remapping: steer with `A`/`D`, the 7 emotes
(`1`–`7`), and over MyWhoosh Link: camera view, U-turn and tuck.

**About MyWhoosh's keys:** MyWhoosh only documents `I`/`K` for shifting, not
which way each one goes. zwhoosh uses `K` = harder and `I` = easier, which is
what in-game testing ([gearpad.app](https://gearpad.app/guides/mywhoosh-keyboard-shortcuts))
and BikeControl's MyWhoosh keymap use. If your gears go the wrong way, press
`x`. Steering, `H`, `U` and emotes are community-reported. `Esc` for pause is
an assumption.

## Zwift Ride firmware 1.3+ ("Ride V2") and Click v2

Zwift locks newer controllers to its own app:

- **Zwift Ride on firmware ≤ 1.2** works directly.
- **Zwift Ride on firmware 1.3+** hides its controls until the Zwift app has
  unlocked it. zwhoosh shows it as **locked**.
- **Zwift Click v2** connects but stops sending button presses after about a
  minute unless it has been unlocked.

To unlock: open the **Zwift** app (a free account is enough), pair your trainer
and the controller, ride for about 30 seconds, quit Zwift completely, then press
a controller button so zwhoosh connects. The unlock lasts about a day. The
firmware version shows in zwhoosh's Controllers table. Don't update controller
firmware if you can avoid it.

## Delivery modes (`m`)

- **auto-focus** (default): keys only ever go to MyWhoosh. If another app is in
  front, MyWhoosh is activated first.
- **background** (experimental): keys are posted straight to the MyWhoosh
  process without focusing it. Whether MyWhoosh accepts these depends on the
  game.
- **frontmost**: keys go to whatever app is in front.

## MyWhoosh Link (experimental, `l`)

MyWhoosh can be controlled by its *MyWhoosh Link* companion app over the local
network. With Link enabled, zwhoosh listens on TCP port 21587. Once MyWhoosh
connects, shifting, steering, camera, U-turn and tuck go over Link and don't
need keyboard focus. Menu buttons still use the keyboard. MyWhoosh only
connects to a Link device it already knows: open the official MyWhoosh Link app
on this Mac once, make sure it connects, then close it. If MyWhoosh never
connects, keep Link off; the keyboard covers everything except camera, U-turn
and tuck.

## Settings

Settings are saved in `~/.config/zwhoosh/config.json` when you change something
in the UI. You can also edit the file by hand:

```json
{
  "mapping": { "Z": "uturn", "Y": "emote_2" },
  "keys": { "hide_hud": "f5" },
  "delivery": "focus",
  "swap_shift_keys": false,
  "link_enabled": false,
  "paddle_threshold": 25,
  "repeat_delay": 0.45,
  "repeat_interval": 0.18
}
```

Button names are `UP DOWN LEFT RIGHT A B Y Z SHIFT_UP_L SHIFT_DN_L SHIFT_UP_R
SHIFT_DN_R POWERUP_L POWERUP_R ONOFF_L ONOFF_R PADDLE_L PADDLE_R`. Action ids
are listed in `zwiftcontrols/actions.py`.

## Development

```sh
uv sync
uv run zwhoosh          # or: uv run main.py
uv run pytest
```

## Credit

The Zwift controller BLE protocol was reverse-engineered by
[ajchellew](https://github.com/ajchellew/zwiftplay) and
[Makinolo](https://www.makinolo.com/blog/2024/07/26/zwift-ride-protocol/),
packaged into a Python client by
[jat255](https://github.com/jat255/zwift_click_handling), and documented in
detail by [BikeControl](https://github.com/OpenBikeControl/bikecontrol) (Ride
button map, left-hood relay, firmware lock, MyWhoosh Link format). zwhoosh is
an independent implementation; no code is copied from those projects.

## License and disclaimer

[MIT](LICENSE).

zwhoosh is an independent hobby project. It is not affiliated with, endorsed
by or supported by Zwift, Inc. or MyWhoosh. "Zwift", "Zwift Ride", "Zwift
Click" and "MyWhoosh" are trademarks of their respective owners and are used
here only to describe what zwhoosh works with. Use it at your own risk.
