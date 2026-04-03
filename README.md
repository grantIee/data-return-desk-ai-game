# The Return Desk

A speed/accuracy game that demonstrates why higher levels of agentic engineering beat manual work.

## Quick Start

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8888
```

Open `http://localhost:8888` to play.

## Modes

- `Single Player` starts a private run immediately.
- `Async Multiplayer` puts you into a named challenge. Anyone using the same `challenge code` shares the same leaderboard, even if they play at different times.

Each session gets its own timer. The 30-minute clock starts when that session loads its first customer, not when the server starts.

## How to Play

You're a return desk agent. For each customer, you receive 3 files:

1. `Receipt (PDF)` - Check whether a customer photo is included.
2. `Transactions (Excel)` - Calculate the return rate and dollar ratio.
3. `Fraud Report (PowerPoint)` - Find the assessment slide.

Based on these files, decide `ACCEPT` or `DENY`.

## Decision Rules

Evaluate in order. Stop at the first rule that triggers.

| Rule | Condition | Decision |
|------|-----------|----------|
| 1 - Photo Gate | Receipt has NO customer photo | DENY |
| 2 - Loyalty Override | Loyalty tier is `GOLD` | ACCEPT |
| 3 - Fraud Override | Fraud = `not malicious` AND spending = `high` | ACCEPT |
| 4 - Return Rate | Return rate > 20% | DENY |
| 5 - Dollar Exception | Return $ < 80% of purchase $ | ACCEPT |
| 6 - Default | None of the above | DENY |

Order: Rule 1 -> Rule 2 -> Rule 3 -> Rule 4 (with Rule 5 exception) -> Rule 6

## Scoring

`score = correct_decisions * (100 / avg_seconds_per_decision)`

Both speed and accuracy matter.

## API

```text
POST /api/session              - Create or rejoin a session
GET  /api/session/{id}         - Session stats, mode, state, time remaining
GET  /api/session/{id}/next    - Get next customer + file URLs
POST /api/session/{id}/decide  - Submit { "customer_id": "...", "decision": "ACCEPT"|"DENY" }
GET  /api/leaderboard          - Rankings, optionally filtered by mode/challenge
GET  /api/rules                - Decision rules text
GET  /api/status               - App defaults and current sessions
POST /api/admin/reset          - Reset sessions; optionally regenerate customers
```

Session creation accepts:

```json
{
  "name": "Grant",
  "mode": "multiplayer",
  "challenge_code": "friends-night"
}
```

For single-player:

```json
{
  "name": "Grant",
  "mode": "single"
}
```

## Data Loading

The server now prefers loading pre-generated customer profiles from `generated/` instead of regenerating the full dataset on startup. If no generated customers exist, it falls back to generation.

For Vercel builds, `build.py` generates the dataset during the build if `generated/` is absent. `vercel.json` deploys the root [server.py](/Users/grantlee/Desktop/agentic-game/server.py) ASGI app directly and explicitly includes the generated/static files in the bundle.

## Storage Backends

- Local development uses a JSON file at `.data/session_store.json`.
- Vercel uses Upstash/Vercel Redis automatically if either of these env var pairs is present:
  - `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN`
  - `KV_REST_API_URL` + `KV_REST_API_TOKEN`
- If Redis is not configured on Vercel, the app falls back to a temporary file in `/tmp` so the function can boot, but that storage is not durable across cold starts or instances.

Session updates are atomic through the storage layer so browser + bot traffic does not corrupt the same run.

## Deploying To Vercel

1. Create an Upstash Redis database from the Vercel Marketplace, or otherwise provide the Redis REST URL/token env vars above.
2. Optionally set `ADMIN_CODE`, `CUSTOMER_COUNT`, `GAME_DURATION`, and `SESSION_STORE_NAMESPACE`.
3. Deploy the repo to Vercel. The build script will generate customer files if they are not already present.

Notes:

- This app still serves game files from the Python function bundle. The current generated corpus is small enough for that, but if you grow the asset set significantly you should move those files to Blob or object storage.
- Local persistence is only meant for development. Shared production state should use Redis on Vercel.
