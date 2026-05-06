# Return Desk — Load & Cache Mitigation Design

**Date:** 2026-05-05
**Status:** Approved (ready for implementation plan)

## Background

The Return Desk game is a 20-minute solo run where players (or agents) decide ACCEPT/DENY on customers using six deterministic rules. Two problems surfaced together:

1. **DB load.** A single Claude run logged 3,281 decisions in 20 minutes (100% accuracy, 0.20s avg, score 1,670,185), brought down the Vercel deployment, and made the Hall of Fame stop responding.
2. **Brute-forceable.** The customer pool is a fixed 500. Rules are deterministic. A bot can pre-fetch all 500 customer files once, build a `customer_id → ACCEPT/DENY` cache, and then run the timed session as a tight `/next → lookup → /decide` loop. The cache is reusable across all future sessions.

Both problems are connected: the brute-force technique (cache-then-spam) is what generated the load.

## Goals

- **Operational:** keep the app from falling over on Vercel + Upstash quota.
- **Cache-resistance:** make brute-force pay a real per-session cost, even if not eliminate it.
- **Surgical:** keep changes small and low-risk; we don't have meaningful test or QA bandwidth.

## Non-goals

- Score formula changes (current `correct × 100 / avg_time` stays).
- `avg_time` floor.
- Per-session rule perturbation (vetoed: would feel adversarial).
- Larger customer pool / on-demand generation / Vercel Blob (deferred).
- Defeating a determined bot completely.

## Design summary

Three changes, each surgical:

1. **Per-session deck of N=100 customers, no cycling.** Each session draws 100 customers from the global 500 pool by `random.Random(session_id).sample(...)`. When the deck is exhausted, the run ends. Round-cap and subset-size collapse into a single value.
2. **Leaderboard via Redis ZSET.** Replace the per-request fan-out (`SMEMBERS` + N×`GET`) with a sorted set, capped at top 100. ZADD on every `/decide` so the leaderboard stays live. Read becomes O(100) regardless of total session count.
3. **Admin-reset before deploy.** Wipe brute-force-era data; new format starts clean.

## 1. Data model

One field added to `Session`:

```python
customer_indices: list[int]   # length 100, populated at create_session
```

Indices into the global 500-customer pool. Populated once at session creation:

```python
rng = random.Random(session.id)
session.customer_indices = rng.sample(range(len(customers)), DECK_SIZE)
```

`DECK_SIZE = 100` (constant in `server.py` or `models.py`).

**Why store, not re-derive:** ~100 ints in Redis is trivial cost. Storing protects against pool-size changes mid-deployment and makes "what did this session see?" explicit and debuggable.

**Subset selection:** uniform random `sample` from 500. No diversity quotas in v1. Skewed decks (e.g., 80% deny) are stochastic and same for everyone. Add balanced-sample later if complaints surface.

## 2. Game flow

### Customer lookup

```python
# Before
idx = session.current_customer_index % len(customers)
return customers[idx]

# After
deck_pos = session.current_customer_index
if deck_pos >= len(session.customer_indices):
    return None  # deck exhausted
return customers[session.customer_indices[deck_pos]]
```

No modulo, no cycling.

### End conditions

Two ways a session ends:

1. **Timer expires** (existing behavior).
2. **Deck exhausted** — `current_customer_index >= len(customer_indices)` (new).

Both finalize the score and mark `state == "ended"`.

### State derivation

```python
is_complete = current_customer_index >= len(customer_indices)
state = (
    "ready"  if started_at is None
    else "ended" if (is_complete or is_expired())
    else "active"
)
```

### Endpoint behavior

| Endpoint | Behavior change |
|----------|-----------------|
| `GET /next` (deck exhausted) | Returns `{"done": true, "state": "ended"}` + final summary instead of next cycled customer |
| `POST /decide` (deck exhausted) | Returns `403 session_ended` |
| `GET /next` (timer expired) | Unchanged (`403 session_ended`) |
| `GET /session/{id}` | `total_customers` field now reflects deck size (100), not pool size (500) |

### Score formula

Unchanged: `correct × (100 / avg_time)`. Max score is now bounded by `100 × 100 / avg_time` — a cache-hot bot at 0.20s avg caps at ~50K (33× reduction from the 1.67M run).

## 3. Leaderboard

### Today (root cause of the Vercel outage)

```
GET /api/leaderboard
  → list_sessions()
    → SMEMBERS  {ns}:sessions          # 1 op
    → pipeline GET {ns}:session:{sid}  # N ops (every session ever)
  → sort, rank, return all
```

`O(total_sessions_ever)` per request.

### After

New Redis key: `{ns}:leaderboard` — sorted set, member = `session_id`, score = `session.score`.

**Write path** (in `apply_decision`, after the decision is appended and `current_customer_index` is incremented, still inside the existing per-session lock):

```
ZADD {ns}:leaderboard <new_score> <session_id>
ZREMRANGEBYRANK {ns}:leaderboard 0 -101   # keep top 100 by score
```

ZADD fires on every successfully recorded decision (correct or not — score recomputes either way). Skipped for `409 stale_customer` and `403 session_ended` paths since no decision is recorded. Marginal cost: +1 HTTP request per recorded decision (the two ZSET commands can pipeline into one round-trip), bounded at 100 per session lifetime.

**Read path:**

```
GET /api/leaderboard
  → ZREVRANGE {ns}:leaderboard 0 99 WITHSCORES   # 1 op
  → pipeline GET each session_id                  # 100 ops, fixed
  → format, rank, return
```

`O(100)` regardless of total session count.

### Storage layer changes

- `UpstashRedisSessionStore` gets `update_leaderboard_score(session)` (writes ZADD + trim) and `list_top_sessions(limit=100)`.
- `JsonFileSessionStore` (local dev) keeps the O(N) implementation — local volume is tiny, no parallel structures needed.
- The `SessionStore` ABC gains both methods; JSON store implements them via in-memory sort.

### Out of scope for v1

- In-memory response cache in `server.py` — Vercel cold starts limit value; ZSET path is already fast enough.
- ZADD-only-on-end optimization — vetoed for liveness.
- Backfill pass at deploy — admin-reset handles this.

## 4. Rollout & migration

### Deploy sequence

1. **Admin-reset prod first** (existing `/api/admin/reset` endpoint). Wipes session set + brute-force-era data. The 3,281-decision entry goes away.
2. **Deploy the new code.** No customer-pool changes; `generated/` stays at 500.
3. **First post-deploy session** populates `customer_indices` at create-time and hits the new ZSET on first `/decide`. Leaderboard endpoint switches to the ZSET read path. No migration script.

### Backwards compat (defensive, in case admin-reset is skipped)

- `Session.ensure_deck(pool_size)` — called at every mutator entrypoint. If `customer_indices` is empty, populate from `random.Random(self.id).sample(...)`. ~5 lines.
- Pre-existing completed sessions in Redis don't auto-appear on the new ZSET. Leaderboard goes "fresh" with the new format. Acceptable.

### Frontend changes (`static/index.html`)

- "Customer X of 500" → "Customer X of {total_customers}" (API already returns `total_customers`; relabel only).
- Final-screen trigger fires on `state === "ended"` regardless of cause. Should already work; smoke test.
- Optional polish: distinguish "deck complete" from "time's up" copy. Not required to ship.

## 5. Risk register

| Risk | Likelihood | Mitigation |
|------|-----------:|------------|
| Admin-reset forgotten; old data co-exists | Medium | Lazy-populate + ZSET starts empty |
| Random sampling gives all-deny / all-accept deck | Low | Accept v1; add balanced sample if complaints |
| ZADD inside lock slows `/decide` slightly | Low | Existing lock already does 4 ops; +1 is rounding error |
| Legacy in-progress runs have `current_customer_index > 100` | Edge case (only the brute-force runs) | After lazy-populate gives them a 100-deck, `is_complete` flips true; they're treated as ended. Their recorded decisions stay intact. Admin-reset (step 1 of rollout) makes this moot. |
| `random.Random(string_seed)` non-determinism across Python versions | Low | Single Python version per Vercel deployment |

## 6. Expected impact

**Operational:**

- Leaderboard reads: O(total_sessions) → O(100). At 1K sessions, ~10× DB cost reduction; at 10K, ~100×.
- Per-session writes: bounded at 100 decisions × ~6 Redis HTTP requests (lock acquire, GET, transactional SET+SADD, lock release, ZSET pipeline) ≈ 600 HTTP requests per session, vs. ~13K observed for the 3,281-decision brute-force run.
- Max score: bounded at ~50K for a cache-hot bot vs. 1.67M observed.

**Cache-resistance:**

- Bot's per-session cache reuse drops from 100% (deterministic global pool) to ~80% useless (each session sees a fresh 100-of-500 subset). Bot can defeat by pre-caching all 500 once globally — that's a real one-time cost (~7-10 min of analysis), and the brute-force ceiling is now 33× lower.
- This is a meaningful but not crushing mitigation. A determined bot still wins; the 1.67M-score outlier is gone.

## 7. Implementation surface

Files touched:

- `models.py` — add `customer_indices` field, `is_complete` property, update `state`.
- `server.py` — populate `customer_indices` at `create_session`; update `current_customer_for_session`; add deck-exhausted handling in `/next` and `/decide`; trigger live leaderboard updates through storage after successful decisions; switch `/api/leaderboard` to `list_top_sessions`.
- `storage.py` — add `update_leaderboard_score` and `list_top_sessions` to ABC + both implementations.
- `static/index.html` — relabel "of 500" to use `total_customers` from API.
- (No changes to `generate.py`, `rules.py`, `bot_player.py`, `build.py`.)

Estimated scope: ~150-200 lines changed across 4 files.
