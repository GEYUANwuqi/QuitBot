"""OneBot values and parsing, independent of UIA and storage."""

import json
import re
from dataclasses import asdict, dataclass


class Unsupported(ValueError):
    pass


def numeric_id(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value)):
        raise ValueError("Expected a positive integer ID")
    result = int(value)
    if result > 2**63 - 1:
        raise ValueError("ID exceeds int64")
    return result


@dataclass(frozen=True)
class MessageSegment:
    type: str
    data: dict


@dataclass(frozen=True)
class ApiRequest:
    action: str
    params: dict
    echo: object = None
    has_echo: bool = False

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("action"), str):
            raise TypeError("Request must contain an action string")
        params = value.get("params", {})
        if not isinstance(params, dict):
            raise TypeError("params must be an object")
        return cls(value["action"], params, value.get("echo"), "echo" in value)


@dataclass(frozen=True)
class ApiResponse:
    status: str
    retcode: int
    data: object = None
    message: str | None = None

    def to_dict(self, request=None):
        value = {"status": self.status, "retcode": self.retcode, "data": self.data}
        if self.message is not None:
            value["message"] = value["wording"] = self.message
        if request is not None and request.has_echo:
            value["echo"] = request.echo
        return value


def cq_unescape(value):
    return (
        value.replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&amp;", "&")
    )


def cq_escape(value):
    return value.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")


def parse_segments(value, *, auto_escape=False):
    if isinstance(value, str):
        if auto_escape:
            return [MessageSegment("text", {"text": value})]
        segments = []
        position = 0

        def append_text(raw):
            if "[CQ:" in raw:
                raise ValueError("Malformed CQ code; use auto_escape for literal text")
            segments.append(MessageSegment("text", {"text": cq_unescape(raw)}))

        for match in re.finditer(r"\[CQ:([a-zA-Z0-9_-]+)((?:,[^\]]*)?)\]", value):
            if match.start() > position:
                append_text(value[position : match.start()])
            data = {}
            for parameter in match[2].split(",")[1:]:
                key, separator, content = parameter.partition("=")
                if not key or not separator or key in data:
                    raise ValueError("Malformed CQ segment parameters")
                data[key] = cq_unescape(content)
            segments.append(MessageSegment(match[1], data))
            position = match.end()
        if position < len(value):
            append_text(value[position:])
        return segments
    if not isinstance(value, list):
        raise TypeError("message must be a string or segment array")
    segments = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("type"), str)
            or not isinstance(item.get("data"), dict)
        ):
            raise TypeError("Malformed message segment")
        segments.append(MessageSegment(item["type"], item["data"]))
    return segments


@dataclass(frozen=True)
class OneBotMessageEvent:
    time: int
    self_id: int
    post_type: str
    message_type: str
    sub_type: str
    message_id: int
    user_id: int
    message: list
    raw_message: str
    font: int
    sender: dict
    group_id: int | None = None

    def to_dict(self):
        value = asdict(self)
        if self.group_id is None:
            value.pop("group_id")
        return value


def decode_request(raw):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ValueError("Invalid JSON request") from None
