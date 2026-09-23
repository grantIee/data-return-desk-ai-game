from __future__ import annotations

import hashlib
import os
import secrets
import socket
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from generate import GENERATED_DIR, generate_all_customers, load_generated_customers
from models import (
    Customer,
    DecisionResponse,
    GameMode,
    Session,
    SessionCreate,
    SessionDecision,
    DecisionRequest,
)
from rules import BRIEFING_TEXT, RULES_TEXT, evaluate
from storage import SessionStore, create_session_store

# ── Configuration ──
DEFAULT_CUSTOMER_COUNT = int(os.environ.get("CUSTOMER_COUNT", "500"))
SESSION_DURATION_MINUTES = int(os.environ.get("GAME_DURATION", "20"))
DECK_SIZE = int(os.environ.get("DECK_SIZE", "100"))
ADMIN_CODE = os.environ.get("ADMIN_CODE")
LEADERBOARD_WEEK_SECONDS = 7 * 24 * 60 * 60
LEADERBOARD_RECORD_SLOTS = 3
LEADERBOARD_RECENT_SLOTS = 7

app = FastAPI(title="The Return Desk")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── App state ──
customers: list[Customer] = []
customer_lookup: dict[str, Customer] = {}
admin_code: str = ""
session_store: SessionStore = create_session_store()


def get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "message": message})


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_agent_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(24)
    return token, hash_agent_token(token)


def extract_agent_token(request: Request) -> str | None:
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        token = bearer[7:].strip()
        if token:
            return token

    token = request.headers.get("x-session-token", "").strip()
    return token or None


def require_session_access(session: Session, request: Request) -> None:
    token = extract_agent_token(request)
    token_hash = session.agent_token_hash or ""
    if not token or not token_hash or not secrets.compare_digest(hash_agent_token(token), token_hash):
        raise HTTPException(status_code=401, detail="Invalid or missing session token")


def build_customer_lookup(items: list[Customer]) -> dict[str, Customer]:
    return {customer.id: customer for customer in items}


def load_customer_pool(count: int = DEFAULT_CUSTOMER_COUNT) -> list[Customer]:
    loaded = load_generated_customers(count)
    if loaded:
        return loaded
    return generate_all_customers(count)


def get_session_or_404(session_id: str) -> Session:
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def get_session_state(session: Session, now: float | None = None) -> str:
    now = now or time.time()
    if session.started_at is None:
        return "ready"
    if session.is_expired(now) or session.is_complete:
        return "ended"
    return "active"


def session_total_customers(session: Session) -> int:
    if session.customer_indices:
        return len(session.customer_indices)
    if customers:
        return min(DECK_SIZE, len(customers))
    return DECK_SIZE


def build_decision_history(session: Session) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    correct_so_far = 0

    for index, decision in enumerate(session.decisions, start=1):
        if decision.correct:
            correct_so_far += 1

        history.append(
            {
                "number": index,
                "customer_id": decision.customer_id,
                "decision": decision.decision.value,
                "correct": decision.correct,
                "time_taken": round(decision.time_taken, 2),
                "cumulative_accuracy": round((correct_so_far / index) * 100, 1),
            }
        )

    return history


def best_correct_streak(session: Session) -> int:
    best = 0
    current = 0

    for decision in session.decisions:
        if decision.correct:
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best


def session_summary(session: Session, now: float | None = None) -> dict:
    now = now or time.time()
    state = get_session_state(session, now)
    elapsed_from = session.started_at or session.created_at
    history = build_decision_history(session)
    times = [decision.time_taken for decision in session.decisions]

    return {
        "id": session.id,
        "name": session.name,
        "mode": session.mode.value,
        "challenge_code": session.challenge_code,
        "state": state,
        "duration_minutes": session.duration_minutes,
        "remaining_seconds": session.remaining_seconds(now),
        "remaining_minutes": round(session.remaining_seconds(now) / 60, 1),
        "correct": session.correct_count,
        "total": session.total_count,
        "accuracy": round(session.accuracy * 100, 1),
        "avg_time": round(session.avg_time, 2),
        "score": round(session.score, 1),
        "elapsed": round(max(0.0, now - elapsed_from), 1),
        "current_customer_index": session.current_customer_index,
        "total_customers": session_total_customers(session),
        "best_streak": best_correct_streak(session),
        "fastest_time": round(min(times), 2) if times else None,
        "slowest_time": round(max(times), 2) if times else None,
        "decision_history": history,
    }


def _score_sort_key(entry: dict[str, Any]) -> tuple:
    return (-entry["score"], -entry["correct"], entry["avg_time"], entry["session_id"])


def _recency_sort_key(entry: dict[str, Any]) -> tuple:
    return (-entry["started_at"], -entry["score"], -entry["correct"], entry["avg_time"], entry["session_id"])


def build_leaderboard_entries() -> list[dict[str, Any]]:
    now = time.time()
    week_cutoff = now - LEADERBOARD_WEEK_SECONDS
    entries = []
    for session in session_store.list_top_sessions(limit=100):
        if session.total_count == 0:
            continue

        deck_len = len(session.customer_indices) or DECK_SIZE
        if session.total_count > deck_len:
            continue

        started_at = session.started_at if session.started_at is not None else session.created_at
        entries.append(
            {
                "session_id": session.id,
                "name": session.name,
                "state": get_session_state(session, now),
                "correct": session.correct_count,
                "total": session.total_count,
                "accuracy": round(session.accuracy * 100, 1),
                "avg_time": round(session.avg_time, 2),
                "score": round(session.score, 1),
                "started_at": started_at,
                "in_week": started_at >= week_cutoff,
            }
        )

    entries.sort(key=_score_sort_key)

    records = entries[:LEADERBOARD_RECORD_SLOTS]
    record_ids = {entry["session_id"] for entry in records}
    rest = [entry for entry in entries if entry["session_id"] not in record_ids]
    fresh = [entry for entry in rest if entry["in_week"]]
    fresh.sort(key=_score_sort_key)
    older = [entry for entry in rest if not entry["in_week"]]
    older.sort(key=_recency_sort_key)
    recent = (fresh + older)[:LEADERBOARD_RECENT_SLOTS]

    for index, entry in enumerate(records, start=1):
        entry["section"] = "record"
        entry["rank"] = index
    for index, entry in enumerate(recent, start=1):
        entry["section"] = "recent"
        entry["rank"] = index

    return records + recent


def current_customer_for_session(session: Session) -> Customer | None:
    if not customers:
        raise HTTPException(status_code=500, detail="Customer pool is empty")

    if not session.customer_indices or session.is_complete:
        return None

    pool_index = session.customer_indices[session.current_customer_index]
    return customers[pool_index]


def _touch_current_customer(session: Session, now: float) -> Session:
    session.ensure_started(now)
    session.ensure_deck(pool_size=len(customers), deck_size=DECK_SIZE)
    if session.is_expired(now):
        return session

    customer = current_customer_for_session(session)
    if customer is None:
        session.current_customer_id = None
        session.current_customer_started_at = None
        return session

    if session.current_customer_id != customer.id or session.current_customer_started_at is None:
        session.current_customer_id = customer.id
        session.current_customer_started_at = now
    return session


@app.on_event("startup")
async def startup():
    global customers, customer_lookup, admin_code

    customers = load_customer_pool(DEFAULT_CUSTOMER_COUNT)
    customer_lookup = build_customer_lookup(customers)
    admin_code = ADMIN_CODE or secrets.token_hex(3)

    accept = sum(1 for customer in customers if customer.correct_decision == "ACCEPT")
    deny = len(customers) - accept

    print("\nTHE RETURN DESK")
    print("=" * 50)
    print(f"Loaded {len(customers)} customers ({accept} ACCEPT, {deny} DENY)")
    print(f"Session duration: {SESSION_DURATION_MINUTES} minutes")
    print(f"Deck size: {DECK_SIZE}")
    print(f"Session store: {session_store.backend_name}")
    print(f"Admin code: {admin_code}")
    print(f"Local: http://localhost:8888")
    print(f"LAN:   http://{get_local_ip()}:8888")
    print("=" * 50 + "\n")


# ── Static ──

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── App info ──

@app.get("/api/status")
async def get_status():
    sessions = session_store.list_sessions()
    return {
        "storage": session_store.backend_name,
        "customer_count": len(customers),
        "duration_minutes": SESSION_DURATION_MINUTES,
        "session_count": len(sessions),
        "active_sessions": sum(1 for session in sessions if get_session_state(session) != "ended"),
    }


@app.get("/api/time")
async def get_time(request: Request, session_id: str | None = None):
    if session_id:
        session = get_session_or_404(session_id)
        require_session_access(session, request)
        return session_summary(session)

    return {
        "state": "open",
        "remaining_seconds": SESSION_DURATION_MINUTES * 60,
        "remaining_minutes": float(SESSION_DURATION_MINUTES),
        "duration_minutes": SESSION_DURATION_MINUTES,
    }


@app.get("/api/rules")
async def get_rules():
    return {"briefing": BRIEFING_TEXT, "rules": RULES_TEXT}


@app.get("/api/ip")
async def get_ip():
    return {
        "ip": get_local_ip(),
        "local_url": f"http://{get_local_ip()}:8888",
        "tunnel_url": None,
    }


# ── Session endpoints ──

@app.post("/api/session")
async def create_session(req: SessionCreate):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    session_id = str(uuid.uuid4())[:8]
    agent_token, agent_token_hash = create_agent_token()
    session = Session(
        id=session_id,
        name=name,
        mode=GameMode.SINGLE_PLAYER,
        challenge_code=None,
        agent_token_hash=agent_token_hash,
        duration_minutes=SESSION_DURATION_MINUTES,
    )
    session.ensure_deck(pool_size=len(customers), deck_size=DECK_SIZE)
    session_store.save_session(session)

    print(f"  -> Session created: {session.name} ({session.id}) [single]")
    return {
        "session_id": session.id,
        "name": session.name,
        "agent_token": agent_token,
    }


@app.get("/api/session/{session_id}")
async def get_session(session_id: str, request: Request):
    session = get_session_or_404(session_id)
    require_session_access(session, request)
    return session_summary(session)


@app.get("/api/session/{session_id}/next")
async def get_next_customer(session_id: str, request: Request):
    now = time.time()
    session = get_session_or_404(session_id)
    require_session_access(session, request)
    session = session_store.update_session(
        session_id,
        lambda existing: _touch_current_customer(existing, now),
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.is_expired(now):
        return error_response(403, "session_ended", "This run has ended. Check your final score.")

    if session.is_complete:
        return error_response(403, "session_ended", "Run complete. Every customer in your deck has been processed.")

    customer = current_customer_for_session(session)
    assert customer is not None

    return {
        "done": False,
        "state": get_session_state(session, now),
        "remaining_seconds": session.remaining_seconds(now),
        "customer_id": customer.id,
        "customer_name": customer.name,
        "number": session.current_customer_index + 1,
        "total_customers": session_total_customers(session),
        "files": {
            "receipt": f"/api/files/{customer.id}/receipt.pdf",
            "transactions": f"/api/files/{customer.id}/transactions.xlsx",
            "fraud_report": f"/api/files/{customer.id}/fraud_report.pptx",
        },
    }


@app.get("/api/files/{customer_id}/receipt.pdf")
async def get_receipt(customer_id: str):
    path = GENERATED_DIR / customer_id / "receipt.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="application/pdf", filename=f"{customer_id}_receipt.pdf")


@app.get("/api/files/{customer_id}/transactions.xlsx")
async def get_transactions(customer_id: str):
    path = GENERATED_DIR / customer_id / "transactions.xlsx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{customer_id}_transactions.xlsx",
    )


@app.get("/api/files/{customer_id}/fraud_report.pptx")
async def get_fraud_report(customer_id: str):
    path = GENERATED_DIR / customer_id / "fraud_report.pptx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{customer_id}_fraud_report.pptx",
    )


@app.post("/api/session/{session_id}/decide")
async def submit_decision(session_id: str, req: DecisionRequest, request: Request):
    now = time.time()
    response_payload: dict[str, Any] = {}

    session = get_session_or_404(session_id)
    require_session_access(session, request)

    def apply_decision(existing: Session) -> Session | None:
        nonlocal response_payload
        existing.ensure_started(now)
        existing.ensure_deck(pool_size=len(customers), deck_size=DECK_SIZE)

        if existing.is_expired(now):
            response_payload = {
                "type": "error",
                "response": error_response(
                    403,
                    "session_ended",
                    "This run has ended. Check your final score.",
                ),
            }
            return existing

        if existing.is_complete:
            response_payload = {
                "type": "error",
                "response": error_response(
                    403,
                    "session_ended",
                    "Run complete. Every customer in your deck has been processed.",
                ),
            }
            return existing

        customer = current_customer_for_session(existing)
        assert customer is not None

        if req.customer_id != customer.id:
            response_payload = {
                "type": "error",
                "response": error_response(
                    409,
                    "stale_customer",
                    "That customer is no longer current for this session. Refresh and try again.",
                ),
            }
            return existing

        if existing.current_customer_id != customer.id or existing.current_customer_started_at is None:
            existing.current_customer_id = customer.id
            existing.current_customer_started_at = now

        time_taken = max(0.05, now - existing.current_customer_started_at)
        correct_decision, explanation = evaluate(customer)
        is_correct = req.decision == correct_decision

        existing.decisions.append(
            SessionDecision(
                customer_id=req.customer_id,
                decision=req.decision,
                correct=is_correct,
                time_taken=time_taken,
            )
        )
        existing.current_customer_index += 1
        existing.current_customer_id = None
        existing.current_customer_started_at = None
        response_payload = {
            "type": "success",
            "response": DecisionResponse(correct=is_correct, explanation=explanation),
        }
        return existing

    updated = session_store.update_session(session_id, apply_decision)
    if updated is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if response_payload:
        return response_payload["response"]
    raise HTTPException(status_code=500, detail="Decision update failed")


@app.get("/api/leaderboard")
async def get_leaderboard():
    return build_leaderboard_entries()


@app.get("/api/leaderboard/{session_id}")
async def get_public_run_breakdown(session_id: str):
    session = get_session_or_404(session_id)
    if session.mode != GameMode.SINGLE_PLAYER or session.total_count == 0:
        raise HTTPException(status_code=404, detail="Run not found")

    if get_session_state(session) != "ended":
        raise HTTPException(status_code=403, detail="Run breakdowns are public only after a run ends")

    summary = session_summary(session)
    leaderboard_entry = next(
        (entry for entry in build_leaderboard_entries() if entry["session_id"] == session_id),
        None,
    )

    return {
        "id": summary["id"],
        "name": summary["name"],
        "state": summary["state"],
        "rank": leaderboard_entry["rank"] if leaderboard_entry else None,
        "section": leaderboard_entry["section"] if leaderboard_entry else None,
        "in_week": leaderboard_entry["in_week"] if leaderboard_entry else None,
        "started_at": session.started_at if session.started_at is not None else session.created_at,
        "correct": summary["correct"],
        "total": summary["total"],
        "accuracy": summary["accuracy"],
        "avg_time": summary["avg_time"],
        "score": summary["score"],
        "best_streak": summary["best_streak"],
        "fastest_time": summary["fastest_time"],
        "slowest_time": summary["slowest_time"],
        "decision_history": summary["decision_history"],
    }


# ── Admin endpoints ──

@app.post("/api/admin/start")
async def admin_start(body: dict):
    code = body.get("code", "")
    if code != admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")
    return {
        "status": "automatic",
        "message": "Runs now start automatically when a player loads the first customer.",
    }


@app.post("/api/admin/reset")
async def admin_reset(body: dict):
    global customers, customer_lookup, admin_code

    code = body.get("code", "")
    if code != admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")

    regenerate = bool(body.get("regenerate", False))
    customer_count = int(body.get("customer_count", DEFAULT_CUSTOMER_COUNT))

    session_store.reset()
    customers = generate_all_customers(customer_count) if regenerate else load_customer_pool(customer_count)
    customer_lookup = build_customer_lookup(customers)
    admin_code = ADMIN_CODE or secrets.token_hex(3)

    print(f"\nRESET complete. New admin code: {admin_code}")
    return {
        "status": "reset",
        "customers": len(customers),
        "new_admin_code": admin_code,
    }


@app.post("/api/admin/config")
async def admin_config(body: dict):
    global customers, customer_lookup, SESSION_DURATION_MINUTES

    code = body.get("code", "")
    if code != admin_code:
        raise HTTPException(status_code=403, detail="Invalid admin code")

    duration = body.get("duration_minutes")
    if duration is not None:
        SESSION_DURATION_MINUTES = int(duration)

    count = body.get("customer_count")
    if count is not None:
        customers = load_customer_pool(int(count))
        customer_lookup = build_customer_lookup(customers)

    return {
        "status": "reconfigured",
        "customers": len(customers),
        "duration_minutes": SESSION_DURATION_MINUTES,
    }
