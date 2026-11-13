"""Business workflows use file-dialog operations, independent of native widgets."""

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.domain.stickers import FavoriteSticker
from quitbot_backend._engine.errors import MutationOutcomeUnknown, TargetMismatch
from quitbot_backend._engine.ui.workflows.notices import NoticesUI
from quitbot_backend._engine.ui.workflows.sharing import SharingUI
from quitbot_backend._engine.ui.workflows.stickers import StickersUI


class Picker:
    """An opaque dialog deliberately exposes no control tree or Windows IDs."""

    def __init__(self):
        self.open = True
        self.calls = []
        self.path = None
        self.on_confirm = lambda: None
        self.on_path = lambda: None

    def set_path(self, path):
        self.calls.append("path")
        self.path = path
        self.on_path()

    def set_files(self, paths):
        self.calls.append("files")
        self.path = paths

    def confirm(self):
        self.calls.append("confirm")
        self.on_confirm()
        self.open = False

    def cancel(self):
        self.calls.append("cancel")
        self.open = False


def client(picker):
    backend = SimpleNamespace(
        window=object(),
        auto=SimpleNamespace(file_picker=lambda *_: picker if picker.open else None),
        invoke=lambda _: None,
        find=lambda root, predicate: object(),
    )
    return SimpleNamespace(
        backend=backend, control_ui=SimpleNamespace(secondary=object())
    )


@pytest.mark.parametrize("bad_path", [False, True])
@pytest.mark.parametrize(
    "labels", [("选择文件: ", "未选择任何文件"), ("Choose File: ", "No file chosen")]
)
def test_notice_image_uses_opaque_picker_and_cancels_failed_selection(
    tmp_path, bad_path, labels
):
    picker = Picker()
    qq = client(picker)
    source = tmp_path / "fixture.png"
    viewer = object()

    def by_class(name, *_, **__):
        if name == "uploader-viewer":
            return None if picker.open else viewer
        return SimpleNamespace(
            Name=labels[0] + (labels[1] if picker.open else source.name)
        )

    qq.control_ui.by_class = by_class
    qq.backend.walk = lambda _: [SimpleNamespace(ControlTypeName="ImageControl")]
    ui = NoticesUI(qq)
    if bad_path:

        def fail():
            raise TargetMismatch("Clipboard path changed")

        picker.on_path = fail
        with pytest.raises(TargetMismatch):
            ui._choose_image(qq.backend.window, source, fresh=True)
        assert picker.calls == ["path", "cancel"]
    else:
        ui._choose_image(qq.backend.window, source, fresh=True)
        assert picker.calls == ["path", "confirm"]


def test_sticker_import_commits_once_without_native_control_ids(tmp_path):
    paths = [tmp_path / "one.png", tmp_path / "two.png"]
    for path in paths:
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
    picker = Picker()
    ui = StickersUI(client(picker))
    ui.panel = lambda _: nullcontext(object())
    previous = SimpleNamespace(runtime_id=(":1.8", "/existing"))
    added = [SimpleNamespace(runtime_id=(":1.8", f"/new/{i}")) for i in range(2)]
    ui._items = lambda: [previous] if picker.open else [previous, *added]
    result = ui.import_images("20001", paths)
    assert result.imported_count == 2 and result.added == tuple(added)
    assert result.collection == (previous, *added)
    assert picker.path == tuple(paths)
    assert picker.calls == ["files", "confirm"]


@pytest.mark.parametrize("fail_second", [False, True])
def test_single_path_picker_import_does_not_replay_completed_files(
    tmp_path, fail_second
):
    paths = [tmp_path / "one.png", tmp_path / "two.png"]
    for path in paths:
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
    picker = Picker()
    qq = client(picker)
    qq.backend.auto.file_import_batch_size = 1
    qq.backend.invoke = lambda _: setattr(picker, "open", True)
    ui = StickersUI(qq)
    ui.panel = lambda _: nullcontext(object())
    old = SimpleNamespace(runtime_id=(":1.8", "/old"))
    items, submissions = [old], []
    ui._items = lambda: list(items)

    def confirm():
        submissions.append(picker.path)
        if fail_second and len(submissions) == 2:
            raise TargetMismatch("Picker changed after the first file was imported")
        items.append(SimpleNamespace(runtime_id=(":1.8", str(len(submissions)))))

    picker.on_confirm = confirm
    if fail_second:
        with pytest.raises(MutationOutcomeUnknown):
            ui.import_images("20001", paths)
        assert len(items) == 2
    else:
        assert ui.import_images("20001", paths).added == tuple(items[1:])
        assert len(items) == 3
    assert submissions == [(paths[0],), (paths[1],)]


@pytest.mark.parametrize("rebuilt", [False, True])
def test_import_receipt_does_not_guess_new_items_when_collection_identities_change(
    tmp_path, rebuilt
):
    paths = [tmp_path / "one.png", tmp_path / "two.png"]
    for path in paths:
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
    picker = Picker()
    ui = StickersUI(client(picker))
    ui.panel = lambda _: nullcontext(object())
    old = FavoriteSticker("fixture", 1, (1,), "keep", 0)
    added = [
        replace(old, runtime_id=(i + 2,), name="自定义表情", index=i) for i in range(2)
    ]
    current = replace(old, runtime_id=(9,)) if rebuilt else old
    ui._items = lambda: [old] if picker.open else [current, *added]
    result = ui.import_images("20001", paths)
    assert result.imported_count == 2 and result.collection == (current, *added)
    assert result.added == (None if rebuilt else tuple(added))
    assert picker.calls == ["files", "confirm"]


@pytest.mark.parametrize("destination_race", [False, True])
def test_qr_save_preserves_destination_guard_with_opaque_picker(
    tmp_path, destination_race
):
    path = tmp_path / "qr.png"
    picker = Picker()
    ui = SharingUI(client(picker))
    ui.qr = lambda _: nullcontext((SimpleNamespace(id="20001"), object()))
    ui._qr_action = lambda *_: None
    data = b"\x89PNG\r\n\x1a\n" + b"synthetic PNG payload" * 2
    picker.on_confirm = lambda: path.write_bytes(data)
    if destination_race:
        picker.on_path = lambda: path.write_bytes(b"keep")
        with pytest.raises(FileExistsError):
            ui.save_qr("20001", path)
        assert path.read_bytes() == b"keep"
        assert picker.calls == ["path", "cancel"]
    else:
        receipt = ui.save_qr("20001", path)
        assert receipt["size_bytes"] == len(data)
        assert len(receipt["sha256"]) == 64
        assert picker.calls == ["path", "confirm"]
