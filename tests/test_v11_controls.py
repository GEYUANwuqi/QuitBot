import asyncio
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from uia_fixtures import load_tree

from quitbot_backend._engine.adapters.onebot.api import OneBotAdapter
from quitbot_backend._engine.domain.group_settings import GroupJoinPolicy
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import (
    Message,
    Node,
    Target,
    message_ready,
    parse_messages,
)
from quitbot_backend._engine.ui.workflows.essence import parse_essences
from quitbot_backend._engine.ui.workflows.faces import FacesUI
from quitbot_backend._engine.ui.workflows.group_settings import GroupSettingsUI
from quitbot_backend._engine.ui.workflows.notices import NoticesUI


class Control:
    def __init__(self, node):
        self.node = node
        self.ClassName, self.Name = node.class_name, node.name
        self.ControlTypeName = node.type
        self.children = [Control(c) for c in node.children]

    def GetChildren(self):
        return self.children


class Backend:
    @staticmethod
    def walk(root):
        yield root
        for child in root.children:
            yield from Backend.walk(child)

    def find(self, root, predicate, required=True):
        result = next((c for c in self.walk(root) if predicate(c)), None)
        if result is None and required:
            raise ControlUnavailable("Missing fixture control")
        return result

    @staticmethod
    def snapshot(control):
        return control.node


def policy_reader():
    b = Backend()

    def by_class(token, root, required=False):
        return b.find(root, lambda c: token in c.ClassName.split(), required)

    ui = SimpleNamespace(
        b=b,
        by_class=by_class,
        _switch_value=lambda c: "is-active" in c.ClassName.split(),
    )
    return GroupSettingsUI(ui), Control(load_tree("v11_join_policy.json"))


def test_join_policy_uses_section_titles_to_disambiguate_identical_approval_labels():
    reader, panel = policy_reader()
    result = reader._policy(Target("20001", "测试群"), panel)
    assert result == GroupJoinPolicy(
        "20001", "verify", "需要发送验证消息", True, "under100", True, "direct"
    )
    assert (
        reader._dropdown(panel, "邀请加群权限").Name
        != reader._dropdown(panel, "机器人加群权限").Name
    )


def test_join_policy_rejects_multiple_selected_radio_controls():
    reader, panel = policy_reader()
    reader._radio(panel, "允许任何人加群").ClassName += " is-checked"
    with pytest.raises(ControlUnavailable, match="ambiguous"):
        reader._read(panel, "join_mode")


def test_closed_admission_reports_hidden_invitation_settings_as_unobserved():
    reader, panel = policy_reader()
    wrapper = panel.children[0]
    wrapper.children = wrapper.children[:5]
    reader._radio(panel, "需要身份验证").ClassName = "q-radio"
    reader._radio(panel, "不允许任何人加群").ClassName += " is-checked"
    assert reader._policy(Target("20001", "测试群"), panel) == GroupJoinPolicy(
        "20001", "closed", None, None, None, None, None
    )


def test_group_tag_labels_can_be_exposed_only_as_child_text():
    reader, _ = policy_reader()
    panel = Control(
        Node(
            children=(
                Node(type="TextControl", name="群标签（1/5）"),
                Node(
                    type="ListItemControl",
                    children=(
                        Node(
                            class_name="create-group-tag",
                            children=(
                                Node(type="TextControl", name="合成标签"),
                                Node(class_name="q-svg-icon"),
                            ),
                        ),
                    ),
                ),
                Node(type="ListItemControl", class_name="custom-tag-wrapper"),
            )
        )
    )
    assert reader._tags(panel) == ("合成标签",)
    panel.children.append(panel.children[1])
    with pytest.raises(ControlUnavailable, match="ambiguous"):
        reader._tags(panel)


@pytest.mark.parametrize(
    "field,value",
    [
        ("join_mode", []),
        ("join_mode", "unknown"),
        ("member_invites", 1),
        ("bot_invites", "false"),
        ("invite_approval", "open"),
        ("bot_invite_approval", "under100"),
        ("arbitrary", True),
    ],
)
def test_policy_validation_precedes_any_native_navigation(field, value):
    with pytest.raises(ValueError):
        GroupSettingsUI(SimpleNamespace(b=None)).set("1", field, value)


def policy_writer(monkeypatch, uncertain=False):
    baseline = GroupJoinPolicy(
        "20001", "verify", "需要发送验证消息", True, "under100", True, "direct"
    )
    state = {"policy": baseline, "toggles": 0, "pages": 0}

    def toggle(**kwargs):
        state["toggles"] += 1
        state["policy"] = replace(
            state["policy"], member_invites=False, invite_approval=None
        )

    switch = SimpleNamespace(
        IsEnabled=True, GetTogglePattern=lambda: SimpleNamespace(Toggle=toggle)
    )
    ui = SimpleNamespace(b=None, by_class=lambda *a, **k: switch)
    writer = GroupSettingsUI(ui)

    @contextmanager
    def page(*args):
        state["pages"] += 1
        yield Target("20001", "测试群"), object()

    writer.page = page
    writer._policy = lambda *a: state["policy"]
    writer._read = lambda p, f: getattr(state["policy"], f)
    writer._row = lambda *a: None
    if uncertain:

        def failed(read, *args, **kwargs):
            value = read()
            if isinstance(value, GroupJoinPolicy):
                return value
            raise ControlUnavailable("Readback timed out")

        monkeypatch.setattr(
            "quitbot_backend._engine.ui.workflows.group_settings.wait_until", failed
        )
    return writer, state


def test_policy_receipt_preserves_dependent_effects_and_reopens_for_readback(
    monkeypatch,
):
    writer, state = policy_writer(monkeypatch)
    receipt = writer.set("20001", "member_invites", False)
    assert state["toggles"] == 1 and state["pages"] == 2
    assert receipt.before.invite_approval == "under100"
    assert receipt.after.invite_approval is None and not receipt.after.member_invites


def test_uncertain_policy_submission_is_not_replayed(monkeypatch):
    writer, state = policy_writer(monkeypatch, uncertain=True)
    with pytest.raises(MutationOutcomeUnknown):
        writer.set("20001", "member_invites", False)
    assert state["toggles"] == 1


def test_essence_fixture_retains_ui_metadata_without_server_ids():
    for name, kind, preview, sent in (
        ("v11_essence.json", "text", "精华示例消息", "10:03"),
        ("video_essence.json", "video", "samp...o.mp4", "10:05"),
    ):
        result = parse_essences(load_tree(name), "20001", "测试群")
        item = result.items[0]
        assert item.kind == kind and item.text_preview == preview
        assert item.sender_name == "测试账号" and item.operator_text == "由测试账号设置"
        assert item.sent_time_text == sent and item.id is None and not result.complete
    with pytest.raises(TargetMismatch):
        parse_essences(load_tree("v11_essence.json"), "20001", "different")


def test_essence_does_not_report_loading_as_empty():
    title = Node(
        class_name="page-title",
        children=(Node(type="TextControl", name="群精华-测试群"),),
    )
    with pytest.raises(ControlUnavailable):
        parse_essences(Node(children=(title,)), "1", "测试群")
    empty = Node(children=(title, Node(type="TextControl", name="暂无精华消息")))
    assert parse_essences(empty, "1", "测试群").items == ()


def test_quoted_faces_do_not_count_as_new_message_faces():
    item = Node(
        class_name="ml-item",
        automation_id="1",
        children=(
            Node(
                class_name="message-content",
                children=(
                    Node(
                        class_name="reply-element",
                        children=(Node(class_name="face-element"),),
                    ),
                    Node(type="TextControl", name="reply"),
                ),
            ),
        ),
    )
    message = parse_messages(item, "1")[0]
    assert message.face_count == 0 and message.kind == "text"


def test_onebot_does_not_drop_unresolved_faces_or_invent_face_ids():
    adapter = OneBotAdapter(SimpleNamespace(account="测试账号"), 10001)
    try:
        message = parse_messages(load_tree("v11_face_message.json"), "20001")[0]
        assert message.kind == "mixed" and message_ready(message)
        assert message_ready(
            Message("face", "1", "peer", "", "incoming", kind="face", face_count=1)
        )
        event = adapter.message_event(Target("20001", "测试群"), message)
        assert event["detail_type"] == "unresolved_message"
        assert (
            event["reason"] == "face_id_unavailable"
            and event["ui_message"]["face_count"] == 1
        )
    finally:
        adapter.index.close()


@pytest.mark.parametrize("text", [" ", "x" * 601, "invalid\x00text"])
def test_notice_input_rejected_before_touching_ui(text):
    with pytest.raises(ValueError):
        NoticesUI(SimpleNamespace(backend=None)).publish("1", text)


def test_face_input_validation_precedes_native_navigation():
    ui = FacesUI(SimpleNamespace(b=None, client=None))
    with pytest.raises(ValueError):
        ui.send("1", "")
    with pytest.raises(TypeError):
        ui.send("1", "微笑", text=1)


def test_group_only_extensions_reject_private_scopes_before_dispatch():
    async def run():
        target = Target("30001", "peer", "private")
        adapter = OneBotAdapter(
            SimpleNamespace(
                account="测试账号",
                conversation=lambda key: SimpleNamespace(target=target, id=target.id),
            ),
            10001,
        )
        try:
            for action in (
                "x_set_group_join_mode",
                "x_set_group_description",
                "x_publish_group_notice",
                "x_get_group_essences",
            ):
                response = await adapter.handle(
                    {
                        "action": action,
                        "params": {
                            "target_id": target.id,
                            "value": "closed",
                            "text": "test",
                        },
                    }
                )
                assert response["retcode"] != 0
        finally:
            adapter.index.close()

    asyncio.run(run())


def test_announcement_window_matching_deduplicates_handles_without_merging_accounts():
    from quitbot_backend._engine.ui.workflows.notices import matching_windows

    def window(pid, hwnd):
        return SimpleNamespace(
            ProcessId=pid,
            Name="群公告-测试群",
            ControlTypeName="WindowControl",
            NativeWindowHandle=hwnd,
        )

    values = [window(1, 11), window(1, 11), window(2, 22), window(1, 0)]
    backend = SimpleNamespace(
        auto=SimpleNamespace(
            GetRootControl=lambda: SimpleNamespace(GetChildren=lambda: values)
        )
    )
    assert [
        w.NativeWindowHandle for w in matching_windows(backend, 1, "群公告-测试群")
    ] == [11]
    values.append(window(1, 33))
    assert len(matching_windows(backend, 1, "群公告-测试群")) == 2


def test_async_face_returns_receipt_on_the_com_worker_and_onebot_indexes_it():
    import threading

    from quitbot_backend._engine.async_api import AsyncQQInstance
    from quitbot_backend._engine.domain.faces import FaceReceipt
    from quitbot_backend._engine.instance import QQInstance

    def factory(account, **kwargs):
        owner = threading.get_ident()
        target = Target("20001", "测试群")

        def check_thread():
            assert threading.get_ident() == owner

        def send(key, label, *, text=""):
            assert threading.get_ident() == owner
            return FaceReceipt(key.id, "native-face", label, text)

        client = SimpleNamespace(
            backend=SimpleNamespace(
                account=account, hwnd=123, check_thread=check_thread
            ),
            targets=(target,),
            target=lambda key: target,
            control_ui=SimpleNamespace(send_face=send),
        )
        return QQInstance.from_client(client)

    async def run():
        async with await AsyncQQInstance.connect(
            "测试账号", factory=factory, initialize_com=False
        ) as qq:
            receipt = await qq.group("20001").send_face("微笑", text="test")
            assert isinstance(receipt, FaceReceipt) and receipt.face_id is None
            adapter = OneBotAdapter(qq, 10001)
            try:
                response = await adapter.handle(
                    {
                        "action": "x_send_face",
                        "params": {"target_id": "20001", "label": "微笑"},
                    }
                )
                assert response["retcode"] == 0
                assert response["data"]["_quitbot"]["face_id"] is None
                assert adapter.index.resolve(10001, response["data"]["message_id"]) == (
                    "group",
                    "20001",
                    "native-face",
                )
            finally:
                adapter.index.close()

    asyncio.run(run())
