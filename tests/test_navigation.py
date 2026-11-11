from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable
from quitbot_backend._engine.models import Target
from quitbot_backend._engine.uia import UIABackend, navigation_label_matches


@pytest.mark.parametrize("operation", ["navigate", "profile"])
@pytest.mark.parametrize("modal_class", ["send-file-msg q-dialog-main", "#32770"])
def test_pending_modal_blocks_keyboard_navigation_before_any_input(
    monkeypatch, operation, modal_class
):
    from quitbot_backend._engine.infrastructure.uia import inspection

    def forbidden(*args, **kwargs):
        pytest.fail("A pending send/file dialog must not receive activation or Enter")

    monkeypatch.setattr(inspection, "require_unlocked", lambda _: None)
    b = UIABackend.__new__(UIABackend)
    b.window = SimpleNamespace(SetActive=forbidden)
    b.main_window = b.window
    b.auto = SimpleNamespace(SendKeys=forbidden)
    b.open_session = b.open_from_contacts = b.activate_detached = forbidden
    b.walk = lambda _: [
        SimpleNamespace(
            ClassName=modal_class, ControlTypeName="WindowControl", IsOffscreen=False
        )
    ]
    with pytest.raises(ControlUnavailable, match="existing QQ dialog"):
        if operation == "navigate":
            b.open_target(Target("10001", "测试账号", "private"))
        else:
            b.profile("10001", "测试账号", "private")


def test_navigation_uses_visible_content_when_toggle_state_is_stale():
    switched = [False]
    old = SimpleNamespace(
        ToggleState=0, Toggle=lambda **kwargs: switched.__setitem__(0, True)
    )
    nav = SimpleNamespace(
        ControlTypeName="ButtonControl",
        Name="联系人",
        ClassName="nav-item",
        GetPattern=lambda _: old,
    )
    pane = SimpleNamespace(
        ControlTypeName="PaneControl", Name="联系人列表", IsOffscreen=False
    )
    backend = UIABackend.__new__(UIABackend)
    backend.window = object()
    backend.auto = SimpleNamespace(PatternId=SimpleNamespace(TogglePattern=1))
    backend.walk = lambda _: [nav, pane] if switched[0] else [nav]
    backend.navigate("联系人")
    assert switched[0] and old.ToggleState == 0


def test_visible_navigation_view_is_not_toggled_away():
    backend = UIABackend.__new__(UIABackend)
    backend.window = object()
    backend.walk = lambda _: [
        SimpleNamespace(
            ControlTypeName="PaneControl", Name="会话列表", IsOffscreen=False
        )
    ]
    backend.navigate("消息")


def test_unread_badges_do_not_break_navigation_or_match_unrelated_names():
    assert navigation_label_matches("消息 143917条未读", "消息")
    assert navigation_label_matches("联系人 1个通知", "联系人")
    assert navigation_label_matches("消息 99+条未读", "消息")
    assert not navigation_label_matches("消息记录", "消息")
    assert not navigation_label_matches("联系人 1个通知", "消息")


def test_visible_verified_contact_details_are_not_deselected():
    b = UIABackend.__new__(UIABackend)
    row = SimpleNamespace(
        GetPattern=lambda _: (_ for _ in ()).throw(
            AssertionError("must not toggle a valid selection")
        )
    )
    assert (
        b.select_contact_details(
            row, Target("12345", "peer", "private"), lambda: "QQ 12345"
        )
        == "QQ 12345"
    )


def test_cached_selected_row_with_empty_details_is_refreshed_before_routing():
    state = [1]
    details = [""]

    def toggle(**_):
        state[0] = 1
        details[0] = "QQ 12345"

    class Pattern:
        @property
        def ToggleState(self):
            return state[0]

        Toggle = staticmethod(toggle)

    b = UIABackend.__new__(UIABackend)
    b.auto = SimpleNamespace(PatternId=SimpleNamespace(TogglePattern=1))
    row = SimpleNamespace(GetPattern=lambda _: Pattern())
    assert (
        b.select_contact_details(
            row, Target("12345", "peer", "private"), lambda: details[0]
        )
        == "QQ 12345"
    )
    assert state[0] == 1
