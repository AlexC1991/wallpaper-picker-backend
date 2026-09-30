# wallpaper-picker

Run **Wallpaper Engine** wallpapers on Linux.

Wallpaper Engine itself is Windows-only. [`linux-wallpaperengine`][lwe] renders its scenes,
videos and web wallpapers on Linux, but it is a command-line program: you point it at a
project folder and it draws on one screen, and that is the whole workflow. There is no
library, no per-monitor setup, no Workshop browser, and nothing that brings your wallpapers
back after a reboot.

This is that missing layer. It is the backend half of the Linux Wallpaper Engine-style
picker: a **daemon**, a **CLI** and a **Steam Workshop browser**, with a small Qt window on
top if you want one. There is also a separate Electron front end that speaks to this CLI.

## What it does

- **A library.** Reads your installed Workshop items and lists what is playable, with
  thumbnails, type and the author's own settings.
- **Per-monitor wallpapers.** Each output gets its own wallpaper, frame rate, scaling and
  edge clamp. A single engine process can drive several monitors at once, so a spanning
  scene stays in sync instead of tearing.
- **It comes back after a reboot.** A daemon watches the screens and restores your setup,
  including after the compositor restarts or a monitor is plugged in.
- **Pausing.** Stops animating behind full-screen apps, on battery, or below a battery
  percentage you choose, and never for apps you name.
- **A Workshop browser.** Search and download items with `steamcmd`, using your own Steam
  account. Browsing needs a free Steam Web API key; downloading needs a signed-in
  `steamcmd`.
- **Projects are honoured.** A wallpaper's own `project.json` settings become properties you
  can override per monitor, grouped into the same categories its author defined.

## Requirements

- Linux with **Wayland** (wlroots or COSMIC: sway, Hyprland, Wayfire, COSMIC) **or X11**.
- Python **3.10+**.
- [`linux-wallpaperengine`][lwe] on your `PATH`, or set its location in `options`.
- `steamcmd` — only if you want to download from the Workshop.
- A Wallpaper Engine install on the same machine, or a Steam login, so the Workshop content
  is available locally.

Monitor discovery works out of the box on all of these, tried in order:
`cosmic-randr` (COSMIC), `wlr-randr` (wlroots), `xrandr` (X11 and XWayland). If none is
installed it still finds your outputs through `/sys/class/drm`, just without their
left-to-right positions.

## Install

```sh
pipx install git+https://github.com/AlexC1991/wallpaper-picker-backend
```

Or with pip:

```sh
pip install git+https://github.com/AlexC1991/wallpaper-picker-backend
```

The daemon and the CLI have no graphical dependencies. The optional Qt window does:

```sh
pipx install "wallpaper-picker[gui] @ git+https://github.com/AlexC1991/wallpaper-picker-backend"
```

## Use

```sh
wallpaper-picker                       # the Qt window, if you installed [gui]
wallpaper-picker --daemon              # run the background daemon (usually via systemd)
wallpaper-picker list                  # installed wallpapers
wallpaper-picker set "firewatch" left  # pick by title or Workshop id
wallpaper-picker set 1234567 DP-1 --fps 60 --scaling fill
wallpaper-picker set 1234567 both -p schemecolor=#ff8800
wallpaper-picker clear right
wallpaper-picker status                # what is running where
wallpaper-picker browse --search "rain"
wallpaper-picker install 1234567
```

`wallpaper-picker --help` lists everything. Every command also takes `--json` and then
prints a single JSON object and no prose, which is how the desktop front ends talk to it.

### Autostart

```sh
wallpaper-picker autostart --enable     # a desktop entry that starts the daemon at login
```

### Workshop downloads

Browsing uses Steam's official Web API, which needs your own free key from
<https://steamcommunity.com/dev/apikey>. Save it with:

```sh
wallpaper-picker options --set steam_api_key=YOURKEY
```

That page asks for a domain although this is a desktop app — enter `localhost`. Steam does
not check it.

Downloading an item is not anonymous, so it uses `steamcmd` with your account. Sign in once
from a terminal; the credentials are then cached:

```sh
steamcmd +login YOUR_ACCOUNT +quit
wallpaper-picker login-status
```

Steam Guard will ask for a code the first time. After that, downloads work.

```sh
wallpaper-picker save-steam --account YOUR_ACCOUNT
```

## How it fits together

```
your Wallpaper Engine library  ──►  workshop.py   ──► installed wallpapers + properties
        (project.json)                  │
                                        ▼
config.json  ──────────────────►   engine.py      ──► the linux-wallpaperengine argv
   (slots, settings, overrides)         │
                                        ▼
                                   daemon.py  ────► supervises the engine process,
                                        │           restarts it, pauses it
                                        ▼
                              D-Bus  ──►  cli.py  ──►  your shell, or a GUI
```

The daemon owns the engine processes and holds the state. The CLI is a D-Bus client: it
asks the daemon to change something and prints what came back. That is why `--json` is the
only output the apps rely on, and why nothing in the library needs to be running for
`list` to work.

| Path | What it is |
| --- | --- |
| `src/wallpaper_picker/cli.py` | command line, and the JSON interface every front end uses |
| `src/wallpaper_picker/daemon.py` | supervises engines, applies config, D-Bus service |
| `src/wallpaper_picker/engine.py` | builds the `linux-wallpaperengine` command line |
| `src/wallpaper_picker/workshop.py` | finds installed wallpapers, parses `project.json` |
| `src/wallpaper_picker/steam.py` | Workshop search and `steamcmd` downloads |
| `src/wallpaper_picker/monitors.py` | monitor discovery across compositors |
| `src/wallpaper_picker/pause.py` | full-screen, maximised-window and battery pausing |
| `src/wallpaper_picker/gui/` | the optional Qt window |

## Tests

```sh
pip install -e ".[test]"
pytest -q
```

The suite covers the engine command lines, the config round trip, the monitor parsers, the
`project.json` reader, the CLI's JSON contract and the app wiring. It runs without a
compositor, an engine or Steam.

## Notes and limitations

- **Only what `linux-wallpaperengine` supports.** Scene, video and web wallpapers work.
  Some effects and shaders do not render identically on Linux; a wallpaper is only marked
  playable if the engine can actually open it.
- **Steam is still Steam.** Nothing here works around needing Wallpaper Engine (or its
  content) on your account, and downloads go through `steamcmd` exactly as Steam intends.
- **COSMIC background layer.** On COSMIC the engine is sent to the background layer, below
  the desktop icons. If you are on another compositor and your icons disappear behind the
  wallpaper, that is the layer setting, not this program.
- **`project.json` is the author's.** Property names, ranges and defaults come from the
  wallpaper, so a badly authored one will look badly organised here too.

This is an unofficial companion. It is not affiliated with Wallpaper Engine or with
`linux-wallpaperengine`, and it redistributes neither.

## Licence

MIT — see [LICENSE](LICENSE).

[lwe]: https://github.com/Almamu/linux-wallpaperengine