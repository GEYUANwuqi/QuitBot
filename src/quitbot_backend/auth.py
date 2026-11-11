"""Console passwords and fixed-deadline, configurable login sessions."""

import asyncio
import hashlib
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import write_json

SESSION_SECONDS = 4 * 60 * 60
PASSWORD_ITERATIONS = 600_000


class SecurityState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password_salt: str = Field(repr=False, pattern=r"^[0-9a-f]{32}$")
    password_hash: str = Field(repr=False, pattern=r"^[0-9a-f]{64}$")
    must_change_password: bool = True


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128, repr=False)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str = Field(min_length=8, max_length=128, repr=False)
    current_password: str | None = Field(default=None, max_length=128, repr=False)


class SessionInfo(BaseModel):
    expires_at: float
    must_change_password: bool


class SessionCredential(SessionInfo):
    access_token: str = Field(repr=False)


@dataclass(frozen=True)
class Session:
    expires_at: float
    deadline: float
    must_change_password: bool


class AccessKeys:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._failures = 0
        self._retry_after = 0.0
        self._login_attempts = 5
        self._login_retry_seconds = 30
        self._session_seconds = SESSION_SECONDS
        self.initial_password: str | None = None
        if path is not None and path.exists():
            raw = path.read_bytes()
            try:
                self.state = SecurityState.model_validate_json(raw)
                return
            except ValidationError:
                previous = json.loads(raw)
                if not isinstance(previous, dict) or not set(previous) <= {
                    "pairing_token",
                    "access_token_hash",
                }:
                    raise
                backup = path.with_name(f"{path.stem}.backup-{time.time_ns()}.json")
                backup.write_bytes(raw)
        self.initial_password = secrets.token_urlsafe(16)
        self.state = self._password_state(self.initial_password, required=True)
        if path is not None:
            write_json(path, self.state.model_dump())

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS
        ).hex()

    @classmethod
    def _password_state(cls, password: str, *, required=False) -> SecurityState:
        salt = secrets.token_hex(16)
        return SecurityState(
            password_salt=salt,
            password_hash=cls._hash(password, salt),
            must_change_password=required,
        )

    def _matches(self, password: str) -> bool:
        return secrets.compare_digest(
            self._hash(password, self.state.password_salt), self.state.password_hash
        )

    def _issue(self) -> SessionCredential:
        now = time.monotonic()
        self._sessions = {k: s for k, s in self._sessions.items() if s.deadline > now}
        token = "qb_" + secrets.token_urlsafe(32)
        session = Session(
            time.time() + self._session_seconds,
            now + self._session_seconds,
            self.state.must_change_password,
        )
        self._sessions[self.digest(token)] = session
        return SessionCredential(
            access_token=token,
            expires_at=session.expires_at,
            must_change_password=session.must_change_password,
        )

    def login(self, password: str) -> SessionCredential:
        with self._lock:
            if time.monotonic() < self._retry_after:
                remaining = max(1, int(self._retry_after - time.monotonic()) + 1)
                raise HTTPException(429, f"登录尝试过多，请 {remaining} 秒后重试")
            if not isinstance(password, str) or not 1 <= len(password) <= 128:
                raise HTTPException(401, "密码错误")
            if not self._matches(password):
                self._failures += 1
                if self._failures >= self._login_attempts:
                    self._retry_after = time.monotonic() + self._login_retry_seconds
                    self._failures = 0
                raise HTTPException(401, "密码错误")
            self._failures = 0
            return self._issue()

    def configure_login(
        self, attempts: int, retry_seconds: int, session_hours: float = 4
    ) -> None:
        with self._lock:
            self._login_attempts = attempts
            self._login_retry_seconds = retry_seconds
            self._session_seconds = session_hours * 3600

    def session(self, token: str | None, *, allow_password_change=False) -> Session:
        session = (
            self._sessions.get(self.digest(token)) if isinstance(token, str) else None
        )
        if session is None or session.deadline <= time.monotonic():
            raise HTTPException(401, "登录已过期，请重新登录")
        if session.must_change_password and not allow_password_change:
            raise HTTPException(403, "请先修改初始密码")
        return session

    def info(self, token: str, *, allow_password_change=False) -> SessionInfo:
        session = self.session(token, allow_password_change=allow_password_change)
        return SessionInfo(
            expires_at=session.expires_at,
            must_change_password=session.must_change_password,
        )

    def valid(self, token: str | None, *, allow_password_change=False) -> bool:
        try:
            self.session(token, allow_password_change=allow_password_change)
            return True
        except HTTPException:
            return False

    def change_password(self, token: str, body: PasswordChange) -> SessionCredential:
        with self._lock:
            session = self.session(token, allow_password_change=True)
            if not session.must_change_password and (
                body.current_password is None
                or not self._matches(body.current_password)
            ):
                raise HTTPException(401, "当前密码错误")
            if not body.new_password.strip() or self._matches(body.new_password):
                raise HTTPException(400, "请设置与当前密码不同的新密码")
            state = self._password_state(body.new_password)
            self.session(token, allow_password_change=True)
            if self.path is not None:
                write_json(self.path, state.model_dump())
            self.state = state
            self.initial_password = None
            self._sessions.clear()
            self._failures = 0
            self._retry_after = 0.0
            return self._issue()

    def logout(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(self.digest(token), None)


def bearer(header: str | None) -> str | None:
    parts = (header or "").split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


async def guard_session(
    socket: WebSocket,
    keys: AccessKeys,
    token: Callable[[], str],
    *,
    allow_password_change=False,
) -> None:
    """Close idle as well as active sockets on expiry, logout or password change."""
    try:
        while True:
            try:
                session = keys.session(
                    token(), allow_password_change=allow_password_change
                )
            except HTTPException:
                await socket.close(4401, "Session expired or revoked")
                return
            await asyncio.sleep(min(1, max(0, session.deadline - time.monotonic())))
    except (WebSocketDisconnect, RuntimeError):
        pass


async def authenticate_socket(
    socket: WebSocket, keys: AccessKeys, *, allow_login=False
) -> str | None:
    await socket.accept()
    header = socket.headers.get("authorization")
    if header:
        token = bearer(header)
        if keys.valid(token, allow_password_change=allow_login):
            return token
        await socket.close(4401, "Valid session required")
        return None
    echo = {}
    try:
        raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
        if len(raw.encode("utf-8")) > 4096:
            raise ValueError("Authentication request too large")
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) - {"action", "params", "echo"}:
            raise ValueError("Invalid authentication request")
        if "echo" in request:
            echo = {"echo": request["echo"]}
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise TypeError("Invalid authentication parameters")
        if allow_login and request.get("action") == "x_login":
            body = LoginRequest.model_validate(params)
            credential = await asyncio.to_thread(keys.login, body.password)
            token, data = credential.access_token, credential.model_dump()
        elif request.get("action") == "x_authenticate" and set(params) == {
            "access_token"
        }:
            token = params["access_token"]
            data = keys.info(token, allow_password_change=allow_login).model_dump()
        else:
            raise HTTPException(401, "请先登录")
        await socket.send_json({"status": "ok", "retcode": 0, "data": data, **echo})
        return token
    except (
        TimeoutError,
        HTTPException,
        ValueError,
        TypeError,
        RecursionError,
    ) as error:
        await socket.send_json(
            {
                "status": "failed",
                "retcode": 1000 + error.status_code
                if isinstance(error, HTTPException)
                else 1401,
                "data": None,
                "message": error.detail
                if isinstance(error, HTTPException)
                else "登录请求无效",
                **echo,
            }
        )
        await socket.close(4401, "Authentication failed")
        return None
    except WebSocketDisconnect:
        return None
