# eLmi 3005

A pure-Python single-agent research & coding assistant with a retro Linux
terminal GUI. Built for machines with **Windows Smart App Control (SAC)**
enabled — it ships **zero compiled dependencies**, so nothing gets blocked.

- **20 tools** · real web search, memory, read-only coding helpers, and a
  *gated* real terminal (PowerShell / Python) with a persistent working dir.
- **Retro CRT GUI** — boots like an old Unix box, bold ASCII **eLmi** logo
  dead-center, phosphor-green text, amber accent, and a Google-style centered
  search bar with file attachments.
- **SAC-clean** — only the Python standard library + `python-dotenv`
  (a pure-Python package). No Pillow, no compiled wheels.

---

## Features

| Area | Details |
|---|---|
| **Toolkit** | `websearch`, `bing`, `list_files`, `read_file`, `search_code`, `check_syntax`, `run_python`, `run_command`, `change_directory`, `get_pwd`, `get_word_length`, `remember`, `recall` & more (20 total). |
| **Real terminal** | Persistent working dir across calls. Gated behind `--allow-exec` or a live approval prompt. |
| **Memory** | Conversation memory + persistent notes via `remember` / `recall`. |
| **GUI** | tkinter retro terminal. Attach documents & images, live tool-call traces, structured chat blocks, real emojis. |
| **Permission gates** | Exec + filesystem tools are off by default; enabled per-session via flags or interactive approval. Auto-deny in `--once` mode. |

---

## Requirements

- **Python 3.10+** (tested on 3.12). 
- An **OpenRouter API key** (the model in use is `minimax/minimax-m3:free`).
- No compiled packages needed.

---

## Setup

```powershell
# 1. Create + activate a virtual environment
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install the one pure-Python dependency
pip install -r agent\requirements.txt

# 3. Provide your API key
copy agent\.env.template agent\.env
#    then edit agent\.env and set OPENROUTER_API_KEY=sk-or-v1-...
```

> The `.env` file is git-ignored — your key is never committed.

---

## Run — GUI

```powershell
cd agent
..\venv\Scripts\python.exe gui.py --allow-exec
```

- `--allow-exec` enables real terminal execution (PowerShell / Python).
- `--allow-dir "C:/path"` pre-approves filesystem tools for a directory.
- Drop either flag to require interactive approval, or to run fully read-only.

## Run — CLI

```powershell
py agent.py --once "What is the weather in Paris?"
```

Flags mirror the GUI: `--allow-exec`, `--allow-dir <path>`, `--once`.

---

## Layout

```
elmi-3005-agent/
├── agent/
│   ├── agent.py            # the agent engine (20 tools, permission gates)
│   ├── gui.py              # retro CRT tkinter chat GUI
│   ├── tools.py            # shared tool signatures / helpers
│   ├── requirements.txt    # ONLY python-dotenv
│   ├── .env.template       # copy to .env and add your key
│   └── .gitignore
├── test_setup.py           # quick environment sanity check
└── README.md
```

---

## Safety notes

- **Exec is off by default.** Nothing runs without your explicit approval.
- `memory.json`, `.env`, and the build output are git-ignored.
- The agent model is `minimax/minimax-m3:free` — override with `ELMI_MODEL` in `.env`.

Built with zero compiled dependencies so Smart App Control stays happy. 🖤
