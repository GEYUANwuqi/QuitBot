"""Daily local diagnostics, safe exception formatting and transport receipts."""

import copy
import json
import logging
import re
import sys
import time
import traceback
from contextvars import ContextVar
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException
from websockets.exceptions import WebSocketException

log = logging.getLogger(__name__)
transport = ContextVar("diagnostic_transport", default="internal")
request_id = ContextVar("diagnostic_request_id", default="-")
_CREDENTIAL = re.compile(
    r"""(?i)(["']?(?:access_token|current_password|new_password|password|password_hash|password_salt|pairing_token)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]&]+)"""
)


def validation_details(error):
    return [
        {key: issue[key] for key in ("loc", "type", "msg") if key in issue}
        for issue in error.errors()
    ]


class DiagnosticFormatter(logging.Formatter):
    def __init__(self):
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s request=%(request_id)s %(message)s"
        )
        self.secrets = ()

    def formatException(self, info):
        trace = traceback.TracebackException(*info, capture_locals=False)

        def sanitize(item, error):
            if isinstance(error, (ValidationError, RequestValidationError)):
                # Pydantic's default text includes input values, including passwords.
                item._str = json.dumps(validation_details(error), ensure_ascii=False)
            elif isinstance(error, WebSocketException):
                item._str = "Remote details omitted; see the recorded HTTP / close code"
            if item.__cause__ is not None:
                sanitize(item.__cause__, error.__cause__)
            if item.__context__ is not None:
                sanitize(item.__context__, error.__context__)
            for child, original in zip(
                item.exceptions or (), getattr(error, "exceptions", ())
            ):
                sanitize(child, original)

        sanitize(trace, info[1])
        return "".join(trace.format())

    def format(self, record):
        record = copy.copy(record)
        record.request_id = request_id.get()
        record.exc_text = None
        value = super().format(record)
        for secret in self.secrets:
            if len(secret) >= 8:
                value = value.replace(secret, "[REDACTED]")
            else:
                value = re.sub(
                    r"(?<!\w)" + re.escape(secret) + r"(?!\w)", "[REDACTED]", value
                )
        value = _CREDENTIAL.sub(r"\1[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+[^\s,'\"}\]]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bqb_[A-Za-z0-9_-]+", "[REDACTED]", value)
        return re.sub(r"(?i)((?:https?|wss?)://)[^/\s@]+@", r"\1[REDACTED]@", value)


class DailyLogHandler(logging.FileHandler):
    """Keep the configured number of calendar days, including today."""

    def __init__(self, directory: Path, retention_days=None):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.retention_days = retention_days
        self.day = date.today()
        self._cleaned_day = None
        super().__init__(
            self.directory / f"quitbot-{self.day.isoformat()}.log",
            encoding="utf-8",
            errors="backslashreplace",
        )
        self.setFormatter(DiagnosticFormatter())
        self.maintain(force=True)

    def maintain(self, *, force=False):
        with self.lock:
            today = date.today()
            if not force and today == self.day and today == self._cleaned_day:
                return
            if today != self.day:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                self.baseFilename = str(
                    self.directory / f"quitbot-{today.isoformat()}.log"
                )
                self.stream = self._open()
                self.day = today
            if self.retention_days is None:
                return  # Read the saved policy before removing any historical logs.
            cutoff = today - timedelta(days=self.retention_days - 1)
            for path in self.directory.glob("quitbot-????-??-??.log"):
                try:
                    day = date.fromisoformat(path.stem.removeprefix("quitbot-"))
                except ValueError:
                    continue
                if day < cutoff and path.is_file():
                    path.unlink(missing_ok=True)
            self._cleaned_day = today

    def configure(self, retention_days, secrets=()):
        with self.lock:
            self.retention_days = retention_days
            self._cleaned_day = None
            self.formatter.secrets = tuple(secret for secret in secrets if secret)
            self.maintain(force=True)

    def emit(self, record):
        try:
            try:
                self.maintain()
            except OSError:
                self.handleError(record)
            super().emit(record)
        except Exception:  # noqa: BLE001 - logging failures must not abort submitted operations
            self.handleError(record)

    def handleError(self, record):
        # Standard logging dumps raw message arguments on I/O failure.
        # Keep diagnostics out of business control flow and credentials off stderr.
        try:
            sys.stderr.write(
                "QuitBot 日志写入或清理失败，请检查 logs/ 的权限和剩余空间。\n"
            )
        except OSError:
            pass


def configure_logging(directory: Path) -> DailyLogHandler:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(DiagnosticFormatter())
    root.addHandler(console)
    handler = DailyLogHandler(directory)
    root.addHandler(handler)
    return handler


def log_reply(action, reply, *, account=None, error=None, elapsed=None):
    """Record receipt metadata; successful payloads, params and echo stay private."""
    failed = reply.get("status") != "ok"
    details = {
        "transport": transport.get(),
        "account": account,
        "action": action if isinstance(action, str) else "<invalid>",
        "status": reply.get("status"),
        "retcode": reply.get("retcode"),
    }
    if elapsed is not None:
        details["duration_ms"] = round(elapsed * 1000, 1)
    if failed:
        details.update(message=reply.get("message"), data=reply.get("data"))
        if isinstance(error, (ValidationError, RequestValidationError)):
            details["message"] = "Validation failed (input values omitted)"
            details["validation_errors"] = validation_details(error)
    elif isinstance(reply.get("data"), dict) and reply["data"].get("errors"):
        details["application_errors"] = reply["data"]["errors"]
    log.log(
        logging.ERROR if failed else logging.INFO,
        "Action %s",
        json.dumps(details, ensure_ascii=False, default=str),
        exc_info=(type(error), error, error.__traceback__) if error else None,
        stack_info=failed and error is None,
    )


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        token = transport.set(scope["type"])
        identity = request_id.set(uuid4().hex[:12])
        started, status = time.monotonic(), None

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "websocket.close":
                log.info("WebSocket closed code=%s", message.get("code", 1000))
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception:
            scope["diagnostic_error_logged"] = True
            if scope["type"] == "http":
                status = status or 500
                log.exception("Unhandled request failure http_status=%s", status)
            else:
                log.exception("Unhandled WebSocket failure")
            raise
        finally:
            if scope["type"] == "http":
                route = scope.get("route")
                # Never record a query string, request body or arbitrary URL path.
                failed = status is not None and status >= 400
                log.log(
                    logging.ERROR if failed else logging.INFO,
                    "HTTP method=%s route=%s http_status=%s duration_ms=%.1f",
                    scope["method"],
                    getattr(route, "path", "<unmatched>"),
                    status,
                    (time.monotonic() - started) * 1000,
                    stack_info=failed and not scope.get("diagnostic_error_logged"),
                )
            transport.reset(token)
            request_id.reset(identity)


def instrument_app(app):
    app.add_middleware(RequestLogMiddleware)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        request.scope["diagnostic_error_logged"] = True
        log.error(
            "HTTP failure http_status=%s message=%s",
            error.status_code,
            error.detail,
            exc_info=(type(error), error, error.__traceback__),
        )
        return await http_exception_handler(request, error)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        request.scope["diagnostic_error_logged"] = True
        issues = validation_details(error)
        log.error(
            "HTTP validation failure http_status=422 data=%s",
            issues,
            exc_info=(type(error), error, error.__traceback__),
        )
        return await request_validation_exception_handler(request, error)
