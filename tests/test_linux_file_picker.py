from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import FocusLost, TargetMismatch
from quitbot_backend._engine.infrastructure.atspi.file_picker import FilePicker
from quitbot_backend._engine.infrastructure.atspi.x11 import Window


def picker_fixture():
    window = Window(10, 20, "Open Files", "qq Qq", (0, 0, 640, 480), True)
    auto = SimpleNamespace(desktop=SimpleNamespace(foreground=lambda: 10))
    picker = FilePicker(auto, window, object())
    auto.find_file_picker = lambda owner: picker
    return auto, picker


def test_picker_refuses_replaced_owner_window_or_lost_focus():
    auto, picker = picker_fixture()
    picker.check()
    auto.desktop.foreground = lambda: 11
    with pytest.raises(FocusLost):
        picker.check()
    auto.find_file_picker = lambda owner: None
    with pytest.raises(TargetMismatch):
        picker.check()


def test_picker_does_not_confirm_unverified_path(monkeypatch):
    auto, picker = picker_fixture()
    calls = []
    auto.desktop.activate = lambda *args: None
    auto.desktop.set_clipboard = lambda value: calls.append(value)
    auto.SendKeys = lambda keys, **_: calls.append(keys)
    monkeypatch.setattr(picker, "copy_field", lambda: "/unexpected.txt")
    with pytest.raises(TargetMismatch, match="path verification"):
        picker.set_path("/fixture.txt")
    assert "{Enter}" not in calls


def test_save_picker_checks_original_filename_before_replacement(monkeypatch):
    auto, picker = picker_fixture()
    calls = []
    auto.desktop.activate = lambda *args: None
    auto.SendKeys = lambda keys, **_: calls.append(keys)
    monkeypatch.setattr(picker, "copy_field", lambda: "another-file.txt")
    with pytest.raises(TargetMismatch, match="selected file"):
        picker.set_path("/fixture.txt", expected_name="fixture.txt")
    assert calls == ["{Alt}n"]


def test_folder_picker_accepts_native_trailing_slash_but_not_another_folder(
    monkeypatch,
):
    auto, picker = picker_fixture()
    auto.desktop.activate = lambda *args: None
    auto.desktop.set_clipboard = lambda value: None
    auto.SendKeys = lambda keys, **_: None
    monkeypatch.setattr(picker, "copy_field", lambda: "/fixture/")
    picker.set_path("/fixture", directory=True)
    monkeypatch.setattr(picker, "copy_field", lambda: "/fixture-other/")
    with pytest.raises(TargetMismatch):
        picker.set_path("/fixture", directory=True)


def test_folder_submission_is_not_repeated_after_dialog_closes():
    auto, picker = picker_fixture()
    picker.directory = True
    calls = []

    def send(keys, **options):
        calls.append(keys)
        auto.find_file_picker = lambda owner: None

    auto.SendKeys = send
    picker.confirm()
    assert calls == ["{Enter}"]
