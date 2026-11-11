"""Account configuration and independently configurable network transports."""

import hashlib
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    WebsocketUrl,
    field_validator,
    model_validator,
)


def listen_host(value: str) -> str:
    value = value.strip().removeprefix("[").removesuffix("]")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if (
            not value
            or len(value) > 253
            or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                for part in value.split(".")
            )
        ):
            raise ValueError("监听地址填写 IP 或主机名，端口单独设置") from None
        return value.lower()


class ListenerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    host: str = "127.0.0.1"
    port: int = Field(default=6700, ge=1024, le=65535)

    _host = field_validator("host")(listen_host)


class ReverseWSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    url: str = ""
    role: Literal["Universal", "API", "Event"] = "Universal"
    access_token: str = Field(
        default="", repr=False, pattern=r"^[\x21-\x7e]*$", max_length=4096
    )
    reconnect_interval: float = Field(default=3, ge=0.2, le=300, allow_inf_nan=False)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value:
            return value
        if any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("反向 WS 地址不能包含空白或控制字符")
        url = WebsocketUrl(value)
        if any(
            v is not None for v in (url.username, url.password, url.query, url.fragment)
        ):
            raise ValueError("反向 WS 地址只填写 ws:// 或 wss:// 地址，令牌单独配置")
        return str(url)

    @model_validator(mode="after")
    def require_url(self):
        if self.enabled and not self.url:
            raise ValueError("启用反向 WS 时必须填写接收地址")
        return self


class TargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[1-9][0-9]*$")
    name: str = Field(min_length=1)
    kind: Literal["group", "private"] = "group"


class MemberBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str = Field(pattern=r"^[1-9][0-9]*$")
    name: str = Field(min_length=1)
    user_id: str = Field(pattern=r"^[1-9][0-9]*$")


class AccountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(pattern=r"^[1-9][0-9]*$")
    label: str = ""
    http: ListenerConfig = Field(default_factory=ListenerConfig)
    forward_ws: ListenerConfig = Field(
        default_factory=lambda: ListenerConfig(port=6701)
    )
    reverse_ws: ReverseWSConfig = Field(default_factory=ReverseWSConfig)
    hwnd: int | None = None
    targets: list[TargetConfig] = Field(default_factory=list)
    auto_connect: bool = False
    mode: Literal["detached", "switch"] = "detached"
    refresh_detached: bool = False
    interval: float = Field(default=1, ge=0.2)
    activities: bool = False
    resolve_members: bool = False
    track_roster: bool = False
    request_kinds: list[Literal["group", "friend"]] = Field(
        default_factory=lambda: ["group", "friend"]
    )
    member_bindings: list[MemberBinding] = Field(default_factory=list)
    origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    @model_validator(mode="before")
    @classmethod
    def split_listener(cls, value):
        # Preserve existing runtime files when opening them in the new editor.
        if isinstance(value, dict) and ("host" in value or "port" in value):
            value = dict(value)
            endpoint = {
                "enabled": True,
                "host": value.pop("host", "127.0.0.1"),
                "port": value.pop("port", 6700),
            }
            value.setdefault("http", endpoint)
            value.setdefault("forward_ws", endpoint)
        return value

    @model_validator(mode="after")
    def validate_targets(self):
        ids = [t.id for t in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("Target IDs must be unique within an account")
        if self.refresh_detached and self.mode != "detached":
            raise ValueError("refresh_detached requires detached mode")
        groups = {t.id for t in self.targets if t.kind == "group"}
        keys = [(b.group_id, b.name) for b in self.member_bindings]
        if len(keys) != len(set(keys)) or any(
            b.group_id not in groups for b in self.member_bindings
        ):
            raise ValueError(
                "Member bindings must uniquely reference configured groups"
            )
        return self


class WebUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=6699, ge=1024, le=65535)
    login_attempts: int = Field(default=5, ge=1, le=20)
    login_retry_seconds: int = Field(default=30, ge=1, le=3600)
    session_hours: float = Field(default=4, ge=0.1, le=720, allow_inf_nan=False)

    _host = field_validator("host")(listen_host)


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webui: WebUIConfig = Field(default_factory=WebUIConfig)
    accounts: list[AccountConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_accounts(self):
        if len({a.account_id for a in self.accounts}) != len(self.accounts):
            raise ValueError("One service per account: duplicate account_id")
        ports = {}
        for account in self.accounts:
            for endpoint in (account.http, account.forward_ws):
                if not endpoint.enabled:
                    continue
                if endpoint.port == self.webui.port:
                    raise ValueError("账号端口不能与 WebUI 端口相同")
                owner = (account.account_id, endpoint.host)
                if endpoint.port in ports and ports[endpoint.port] != owner:
                    raise ValueError("不同账号或监听地址请使用不同端口")
                ports[endpoint.port] = owner
        return self


def load_config(path: str | Path) -> ServiceConfig:
    return ServiceConfig.model_validate_json(Path(path).read_text("utf-8-sig"))


def write_json(path: Path, value: dict) -> None:
    """Replace one complete file; a failed write leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                with self.path.open("x", encoding="utf-8") as stream:
                    stream.write(ServiceConfig().model_dump_json(indent=2) + "\n")
            except FileExistsError:
                pass
        self.current, self.revision = self.read()

    def read(self) -> tuple[ServiceConfig, str]:
        raw = self.path.read_bytes()
        return (
            ServiceConfig.model_validate_json(raw.decode("utf-8-sig")),
            hashlib.sha256(raw).hexdigest(),
        )

    def save(self, config: ServiceConfig, expected_revision: str) -> str:
        current_revision = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if current_revision != expected_revision:
            raise ValueError("配置已被其他窗口或文件修改，请重新读取后再保存")
        write_json(self.path, config.model_dump(mode="json"))
        self.current, self.revision = self.read()
        return self.revision
