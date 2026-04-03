---
name: start-game
description: Set up and launch The Return Desk game server. Use when the user wants to start the game, run the server, play the game, set up the project, or says "start", "play", "run", "launch", or "set up". Handles Python virtual environment creation, dependency installation, customer data generation, and starting the FastAPI server.
---

# Start Game

Set up and launch The Return Desk game server with a single command flow.

## Setup and Launch

1. Check for an existing Python virtual environment at `.venv/`. If missing, create one:

```bash
python3 -m venv .venv
```

2. Activate and install dependencies:

```bash
source .venv/bin/activate && pip install -r requirements.txt
```

3. Check if `generated/` directory exists and has customer folders. If empty or missing, generate customer data:

```bash
source .venv/bin/activate && python generate.py
```

4. Start the server:

```bash
source .venv/bin/activate && uvicorn server:app --host 0.0.0.0 --port 8888
```

Run this in the background so the user can continue interacting.

5. Tell the user the game is running at **http://localhost:8888** and they can open it in their browser.

## Troubleshooting

- If port 8888 is already in use, try port 8889 or ask the user.
- If `python3` is not found, try `python`.
- If pip install fails, ensure Python 3.8+ is installed.
