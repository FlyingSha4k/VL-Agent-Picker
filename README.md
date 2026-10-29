# A-P · Agent Picker

**English** · [Русский](README.ru.md)

A small Windows tool that automatically picks your VALORANT agent on the agent-select screen. It finds agent icons **by image** (so it doesn't care where an agent sits in the list), scrolls the list when the agent is further down, and clicks through AutoHotkey.

> ⚠️ **Read this first.** Riot's rules prohibit third-party automation of game input, and Vanguard may treat it as such. This tool never touches the game process (no memory reading, no injection, no overlay): it only takes screenshots and sends mouse input, like a human would. That lowers the risk but **does not make it zero**. Use it at your own risk, and test in Practice / Custom before anything else. This project is not affiliated with or endorsed by Riot Games.

## Features

- Finds agents by image matching, so a new agent that shifts the list order never breaks anything
- Priority list: if your first pick is taken (icon greyed out), it falls back to the next one
- Remembers how far down the list each agent is and scrolls the mouse wheel before clicking
- Auto mode detects the agent-select screen by itself; global hotkeys (rebindable)
- One-click "train" mode that scrolls the list on its own, captures every page and asks only for the names of new icons
- Standalone `.exe`: stores everything in `%APPDATA%\AgentPicker` and ships its own AutoHotkey runtime
- VALORANT-style dark UI

## Requirements

- Windows 10 / 11
- VALORANT in **Windowed Fullscreen** (borderless); exclusive fullscreen may give a black screenshot
- To build or run from source: Python 3.10+

The UI is in Russian. Geometry is calibrated on a 1920×1080 layout (Russian client) and scales by screen height; other resolutions should work but are less tested.

## Quick start

### Option A: build the exe

1. Put `agent_picker.py`, `build.bat`, `requirements.txt` (and optionally your own `icon.ico`) in one folder.
2. Run `build.bat`. It installs the dependencies, downloads the official AutoHotkey v2 runtime and builds `dist\AgentPicker.exe`.
3. Run `AgentPicker.exe` (it asks for administrator rights; this is needed for hotkeys and clicks to work in the game).

If the AutoHotkey download fails, download it manually from [autohotkey.com](https://www.autohotkey.com/), copy `AutoHotkey64.exe` into a `_build` folder next to `build.bat`, and run the script again.

### Option B: run from source

```
pip install -r requirements.txt
python agent_picker.py
```

Run it as administrator. Without a bundled AutoHotkey it falls back to an installed AutoHotkey v2.

## Usage

1. **Train.** Open the agent-select screen (Practice works), make sure the list is at the very top, click **ОБУЧИТЬ** (Train) and don't touch the mouse. The window minimizes, the program scrolls the list itself, captures every page and asks you for the name of each new icon. It remembers how many wheel notches lead to each agent.
2. **Set priority.** On the main tab select an agent and click **В ПРИОРИТЕТ** (Add to priority). Order is changed with ▲ ▼; the top one is the main pick.
3. **Button position (optional).** If the confirm button is missed, hover over it in the game and press `F7` (or use **ЗАПОМНИТЬ КНОПКУ**).
4. **Run.** Click **ЗАПУСТИТЬ АВТО** (Start auto) or press `F9`. When the agent-select screen appears, the program picks the first available agent from your priority list.

### Default hotkeys

| Key | Action |
|-----|--------|
| `F9` | Auto mode on / off |
| `F11` | Pick right now |
| `F7` | Remember the confirm-button position (cursor on it) |

All of them can be changed in the **НАСТРОЙКИ** (Settings) tab.

### When a new agent is released

Open the agent-select screen and click **ОБУЧИТЬ** again. Known icons are skipped, so it only asks for the new agent's name and updates the scroll data of agents that moved.

## Settings

- **Match threshold**: how strictly an icon must match (default 0.80). Lower it if agents aren't found, raise it if wrong icons get picked.
- **Wheel notches per step**: scroll step used while training (default 3).
- **AHK template**: opens `ahk_template.ahk` so you can change click timings or the number of repeats. Placeholders: `{{NAME}}`, `{{AGENT_X}}`, `{{AGENT_Y}}`, `{{LOCK_X}}`, `{{LOCK_Y}}`.

## How it works

1. A screenshot of the agent grid is compared with saved icon templates (OpenCV template matching). If at least two known icons are visible, the program decides it is on the agent-select screen.
2. For each agent in your priority list it scrolls the list (mouse wheel via AutoHotkey) to the remembered position and looks for the icon. Greyed-out (taken) agents don't match.
3. When it finds a free agent it generates an AutoHotkey script from the template with the exact coordinates and runs it: click the agent, click the confirm button.

## Where the data lives

`%APPDATA%\AgentPicker\`

```
config.json             settings, priority list, hotkeys, button position
ahk_template.ahk        template for generated click scripts
agents\                 icon templates (*.png), positions.json, scrolls.json
ahk\                    generated scripts, _scroll.ahk, runtime\AutoHotkey64.exe
crash.log               written only if the program crashes
```

On first launch the program creates this folder. If a legacy `agents` folder or `picker_gui_config.json` sits next to the program, it is imported automatically.

## Troubleshooting

| Problem | What to try |
|---|---|
| Hotkeys / clicks do nothing in the game | Run as administrator; make sure the game is in borderless mode |
| "No agent found" | Re-run **ОБУЧИТЬ**, lower the match threshold a bit, check that the list is at the top when the screen opens |
| It scrolls too little / too much | Change "Wheel notches per step" and train again |
| Clicks land next to the confirm button | Hover over the button and press `F7` |
| Antivirus flags the exe | Typical for PyInstaller + keyboard hooks. Build it yourself and add an exclusion |
| Something crashed | Look at `%APPDATA%\AgentPicker\crash.log` |

## Project layout

```
agent_picker.py     the whole application
build.bat           builds dist\AgentPicker.exe (PyInstaller + AutoHotkey runtime)
requirements.txt    Python dependencies
icon.ico            optional, used for the exe and window icon
```

## Third-party

- [AutoHotkey v2](https://www.autohotkey.com/) (GPLv2) is downloaded by `build.bat` at build time and bundled into the exe; it is not stored in this repository.
- Built with OpenCV, NumPy, mss, PyAutoGUI, keyboard and Tkinter.

## Disclaimer

Provided as is, without warranty. You are responsible for following the rules of the game and any consequences of using this tool, including account restrictions.
