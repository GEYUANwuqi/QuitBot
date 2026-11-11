from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import DraftNotEmpty
from quitbot_backend._engine.infrastructure.uia.chat import (
    ChatUI,
    action_risk,
    checked_forwarding_labels,
    control_label,
    draft_state,
    forwarding_label,
)
from quitbot_backend._engine.models import Node, Target


def text(value):
    return Node(type="TextControl", name=value)


def test_unnamed_group_controls_use_only_their_own_unique_text_label():
    children = [SimpleNamespace(ControlTypeName="TextControl", Name="图片/视频")]
    group = SimpleNamespace(
        Name="", ControlTypeName="GroupControl", GetChildren=lambda: children
    )
    assert control_label(group) == "图片/视频"
    children.append(SimpleNamespace(ControlTypeName="TextControl", Name="其他文字"))
    assert control_label(group) is None


def test_highlighted_forward_destination_is_not_confused_with_prefix_or_summary():
    row = Node(
        class_name="list-item",
        children=(
            Node(
                class_name="list-item__content",
                children=(
                    text("测试账号"),
                    text("A"),
                    Node(
                        class_name="list-item__summary", children=(text("来自: 测试分组"),)
                    ),
                ),
            ),
        ),
    )
    assert forwarding_label(row, "private") == "测试账号A"
    row = Node(
        class_name="list-item",
        children=(
            Node(
                class_name="list-item__content",
                children=(
                    text("测试群"),
                    text("(3人)"),
                    Node(class_name="list-item__summary", children=(text("包含：测试账号"),)),
                ),
            ),
        ),
    )
    assert forwarding_label(row, "group") == "测试群"


def test_native_mention_is_not_treated_as_image_but_opaque_drafts_are_detected():
    node = Node(
        children=(
            Node(
                class_name="editor-el--inline-block editor-el__at",
                children=(text("@测试账号"),),
            ),
            text(" body"),
        )
    )
    state = draft_state(node)
    assert (
        state.text == "@测试账号 body" and state.mentions == ("@测试账号",) and not state.has_media
    )
    assert draft_state(
        Node(children=(Node(class_name="editor-el--inline-block"),))
    ).has_media
    assert draft_state(
        Node(children=(Node(class_name="editor-el__reply-wrapper"),))
    ).has_quote


def test_compact_forward_picker_verifies_checked_row_without_a_selected_list():
    row = Node(
        class_name="list-item",
        children=(
            Node(class_name="q-checkbox is-checked"),
            Node(class_name="list-item__content", children=(text("测试好"), text("友B"))),
        ),
    )
    other = Node(
        class_name="list-item",
        children=(
            Node(class_name="q-checkbox"),
            Node(class_name="list-item__content", children=(text("other"),)),
        ),
    )
    assert checked_forwarding_labels(Node(children=(row, other)), "private") == ["测试好友B"]


def test_clear_and_send_draft_refuse_unknown_or_opaque_content_before_keyboard_input():
    calls = []
    backend = SimpleNamespace(
        editor=lambda: None,
        snapshot=lambda node: Node(children=(text("keep me"),)),
        auto=SimpleNamespace(SendKeys=calls.append),
    )
    ui = ChatUI(SimpleNamespace(backend=backend))

    @contextmanager
    def checked(key):
        yield Target("1", "测试群")

    ui.checked = checked
    with pytest.raises(DraftNotEmpty):
        ui.clear_draft("1", expected_text="other")
    with pytest.raises(DraftNotEmpty):
        ui.send_draft("1", expected_text="other")
    backend.snapshot = lambda node: Node(
        children=(text("keep me"), Node(class_name="editor-el__reply-wrapper"))
    )
    with pytest.raises(DraftNotEmpty):
        ui.clear_draft("1", expected_text="keep me")
    assert calls == []


def test_pin_is_idempotent_and_verifies_the_toggle_state():
    class Pattern:
        ToggleState = 1
        calls = 0

        def Toggle(self, **kwargs):
            self.calls += 1
            self.ToggleState = 1 - self.ToggleState

    pattern = Pattern()
    control = SimpleNamespace(GetPattern=lambda key: pattern)
    ui = ChatUI(
        SimpleNamespace(
            backend=SimpleNamespace(
                auto=SimpleNamespace(PatternId=SimpleNamespace(TogglePattern=1))
            )
        )
    )

    @contextmanager
    def checked(key):
        yield Target("1", "测试群")

    @contextmanager
    def more():
        yield object()

    ui.checked, ui.more = checked, more
    ui._switch = lambda panel, row: control
    assert ui.set_pinned("1", True).before is True
    assert pattern.calls == 0
    assert ui.set_pinned("1", False).after is False
    assert pattern.calls == 1
    assert ui.set_pinned("1", False).after is False
    assert pattern.calls == 1


def test_settings_panel_is_identified_after_chromium_flattens_transition_wrapper():
    panel = object()
    ui = ChatUI(SimpleNamespace(backend=object()))
    ui.by_class = lambda name, root=None: (
        panel if name in ("side-panel", "set-top") else None
    )
    assert ui._settings_panel() is panel


def test_control_inspection_does_not_imply_destructive_action_support():
    for name in ("删除", "撤回", "退出群聊", "删除好友"):
        assert action_risk(name) == "destructive"
    assert action_risk("屏蔽此人") == "privacy"
    assert action_risk("视频通话") == "capture_or_call"
