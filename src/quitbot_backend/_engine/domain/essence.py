from dataclasses import dataclass


@dataclass(frozen=True)
class EssenceItem:
    sender_name: str
    sent_time_text: str
    operator_text: str
    set_time_text: str
    text_preview: str
    id: None = None
    kind: str = "text"


@dataclass(frozen=True)
class GroupEssences:
    group_id: str
    items: tuple[EssenceItem, ...]
    scope: str = "loaded_visible_text_preview"
    complete: bool = False
