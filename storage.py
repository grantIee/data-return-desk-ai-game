from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from models import GameMode, Session

DEFAULT_SESSION_STORE_PATH = Path(".data/session_store.json")


class StoreError(RuntimeError):
    pass


SessionMutator = Callable[[Session], Session | None]


class SessionStore(ABC):
    backend_name: str = "unknown"

    @abstractmethod
    def get_session(self, session_id: str) -> Session | None:
        raise NotImplementedError

    @abstractmethod
    def save_session(self, session: Session) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_session(self, session_id: str, mutator: SessionMutator) -> Session | None:
        raise NotImplementedError

    @abstractmethod
    def list_sessions(
        self,
        mode: GameMode | None = None,
        challenge_code: str | None = None,
    ) -> list[Session]:
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    def find_active_session(
        self,
        *,
        name: str,
        mode: GameMode,
        challenge_code: str | None,
    ) -> Session | None:
        for session in self.list_sessions(mode=mode, challenge_code=challenge_code):
            if session.name.lower() != name.lower():
                continue
            if session.state == "ended":
                continue
            return session
        return None


class JsonFileSessionStore(SessionStore):
    backend_name = "json-file"

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read_payload(self) -> dict:
        if not self.file_path.exists():
            return {"sessions": {}}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StoreError(f"Session store is corrupted: {self.file_path}") from exc

    def _write_payload(self, payload: dict) -> None:
        tmp_path = self.file_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.file_path)

    @staticmethod
    def _serialize_session(session: Session) -> dict:
        return session.model_dump(mode="json")

    @staticmethod
    def _deserialize_session(raw: dict) -> Session:
        return Session.model_validate(raw)

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            payload = self._read_payload()
            raw = payload.get("sessions", {}).get(session_id)
            if raw is None:
                return None
            return self._deserialize_session(raw)

    def save_session(self, session: Session) -> None:
        with self._lock:
            payload = self._read_payload()
            payload.setdefault("sessions", {})[session.id] = self._serialize_session(session)
            self._write_payload(payload)

    def update_session(self, session_id: str, mutator: SessionMutator) -> Session | None:
        with self._lock:
            payload = self._read_payload()
            raw = payload.get("sessions", {}).get(session_id)
            if raw is None:
                return None

            current = self._deserialize_session(raw)
            updated = mutator(current)
            if updated is None:
                return None

            payload.setdefault("sessions", {})[session_id] = self._serialize_session(updated)
            self._write_payload(payload)
            return updated

    def list_sessions(
        self,
        mode: GameMode | None = None,
        challenge_code: str | None = None,
    ) -> list[Session]:
        with self._lock:
            payload = self._read_payload()
            raw_sessions = payload.get("sessions", {}).values()
            sessions = [self._deserialize_session(raw) for raw in raw_sessions]

        return [
            session
            for session in sessions
            if (mode is None or session.mode == mode)
            and (challenge_code is None or session.challenge_code == challenge_code)
        ]

    def reset(self) -> None:
        with self._lock:
            self._write_payload({"sessions": {}})


class UpstashRedisSessionStore(SessionStore):
    backend_name = "upstash-redis"

    def __init__(self, *, url: str, token: str, namespace: str = "return-desk", timeout: float = 5.0):
        self.url = url.rstrip("/")
        self.token = token
        self.namespace = namespace
        self.timeout = timeout

    @property
    def _sessions_key(self) -> str:
        return f"{self.namespace}:sessions"

    @property
    def _challenges_key(self) -> str:
        return f"{self.namespace}:challenges"

    def _session_key(self, session_id: str) -> str:
        return f"{self.namespace}:session:{session_id}"

    def _challenge_key(self, challenge_code: str) -> str:
        return f"{self.namespace}:challenge:{challenge_code}"

    def _lock_key(self, session_id: str) -> str:
        return f"{self.namespace}:lock:{session_id}"

    def _http_post(self, path: str, body: object) -> object:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise StoreError(f"Upstash request failed: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise StoreError(f"Upstash request failed: {exc}") from exc

    def _command(self, command: list[object]) -> object:
        data = self._http_post("", command)
        if isinstance(data, dict) and "error" in data:
            raise StoreError(str(data["error"]))
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    def _pipeline(self, commands: list[list[object]]) -> list[object]:
        if not commands:
            return []
        data = self._http_post("/pipeline", commands)
        if isinstance(data, dict) and "error" in data:
            raise StoreError(str(data["error"]))
        if not isinstance(data, list):
            raise StoreError(f"Unexpected pipeline response: {data!r}")

        results = []
        for item in data:
            if isinstance(item, dict) and "error" in item:
                raise StoreError(str(item["error"]))
            results.append(item.get("result") if isinstance(item, dict) else item)
        return results

    def _transaction(self, commands: list[list[object]]) -> list[object]:
        if not commands:
            return []
        data = self._http_post("/multi-exec", commands)
        if isinstance(data, dict) and "error" in data:
            raise StoreError(str(data["error"]))
        if not isinstance(data, list):
            raise StoreError(f"Unexpected transaction response: {data!r}")

        results = []
        for item in data:
            if isinstance(item, dict) and "error" in item:
                raise StoreError(str(item["error"]))
            results.append(item.get("result") if isinstance(item, dict) else item)
        return results

    @staticmethod
    def _serialize_session(session: Session) -> str:
        return session.model_dump_json()

    @staticmethod
    def _deserialize_session(raw: str | None) -> Session | None:
        if raw is None:
            return None
        return Session.model_validate_json(raw)

    def get_session(self, session_id: str) -> Session | None:
        result = self._command(["GET", self._session_key(session_id)])
        return self._deserialize_session(result)

    def save_session(self, session: Session) -> None:
        commands: list[list[object]] = [
            ["SET", self._session_key(session.id), self._serialize_session(session)],
            ["SADD", self._sessions_key, session.id],
        ]
        if session.mode == GameMode.MULTI_PLAYER and session.challenge_code:
            commands.append(["SADD", self._challenges_key, session.challenge_code])
            commands.append(["SADD", self._challenge_key(session.challenge_code), session.id])
        self._transaction(commands)

    def _acquire_lock(self, session_id: str, *, ttl_seconds: int = 10, wait_seconds: float = 3.0) -> str:
        owner = f"{time.time_ns()}"
        deadline = time.time() + wait_seconds
        command = ["SET", self._lock_key(session_id), owner, "NX", "EX", ttl_seconds]

        while time.time() < deadline:
            result = self._command(command)
            if result == "OK":
                return owner
            time.sleep(0.05)

        raise StoreError(f"Timed out waiting for lock on session {session_id}")

    def _release_lock(self, session_id: str, owner: str) -> None:
        script = (
            'if redis.call("GET", KEYS[1]) == ARGV[1] '
            'then return redis.call("DEL", KEYS[1]) '
            "else return 0 end"
        )
        try:
            self._command(["EVAL", script, 1, self._lock_key(session_id), owner])
        except StoreError:
            # Best-effort unlock. The lock will also expire automatically.
            return

    def update_session(self, session_id: str, mutator: SessionMutator) -> Session | None:
        owner = self._acquire_lock(session_id)
        try:
            current = self.get_session(session_id)
            if current is None:
                return None

            updated = mutator(current)
            if updated is None:
                return None

            self.save_session(updated)
            return updated
        finally:
            self._release_lock(session_id, owner)

    def list_sessions(
        self,
        mode: GameMode | None = None,
        challenge_code: str | None = None,
    ) -> list[Session]:
        if challenge_code is not None:
            session_ids = self._command(["SMEMBERS", self._challenge_key(challenge_code)]) or []
        else:
            session_ids = self._command(["SMEMBERS", self._sessions_key]) or []

        if not session_ids:
            return []

        commands = [["GET", self._session_key(session_id)] for session_id in session_ids]
        results = self._pipeline(commands)
        sessions = [session for session in (self._deserialize_session(raw) for raw in results) if session is not None]

        return [
            session
            for session in sessions
            if (mode is None or session.mode == mode)
            and (challenge_code is None or session.challenge_code == challenge_code)
        ]

    def reset(self) -> None:
        session_ids = self._command(["SMEMBERS", self._sessions_key]) or []
        challenge_codes = self._command(["SMEMBERS", self._challenges_key]) or []

        keys = [self._sessions_key, self._challenges_key]
        keys.extend(self._session_key(session_id) for session_id in session_ids)
        keys.extend(self._challenge_key(challenge_code) for challenge_code in challenge_codes)
        if keys:
            self._command(["DEL", *keys])


def create_session_store() -> SessionStore:
    redis_url = (
        os.environ.get("UPSTASH_REDIS_REST_URL")
        or os.environ.get("KV_REST_API_URL")
    )
    redis_token = (
        os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        or os.environ.get("KV_REST_API_TOKEN")
    )
    namespace = os.environ.get("SESSION_STORE_NAMESPACE", "return-desk")

    if redis_url and redis_token:
        return UpstashRedisSessionStore(url=redis_url, token=redis_token, namespace=namespace)

    store_path = Path(os.environ.get("SESSION_STORE_PATH", DEFAULT_SESSION_STORE_PATH))
    return JsonFileSessionStore(store_path)
