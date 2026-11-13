"""Local UI references for loaded favorite stickers; not QQ emoji IDs."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FavoriteSticker:
    account: str
    hwnd: int
    runtime_id: tuple[int | str, ...]
    name: str
    index: int


@dataclass(frozen=True)
class StickerImportReceipt:
    imported_count: int
    collection: tuple[FavoriteSticker, ...]
    added: tuple[FavoriteSticker, ...] | None
    verified_in_ui: bool = True
