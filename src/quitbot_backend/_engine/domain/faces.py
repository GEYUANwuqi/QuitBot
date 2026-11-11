from dataclasses import dataclass


@dataclass(frozen=True)
class FaceReceipt:
    target_id: str
    message_id: str
    label: str
    text: str
    face_count: int = 1
    face_id: None = None
    verified_in_ui: bool = True
