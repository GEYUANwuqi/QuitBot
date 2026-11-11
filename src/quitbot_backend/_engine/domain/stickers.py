"""Local UI references for loaded favorite stickers; not QQ emoji IDs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FavoriteSticker:
    account: str
    hwnd: int
    runtime_id: tuple[int, ...]
    name: str
    index: int
