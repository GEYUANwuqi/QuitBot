from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch
from quitbot_backend._engine.models import Node, Target
from quitbot_backend._engine.ui.workflows.navigation import NavigationUI
from quitbot_backend._engine.ui.workflows.sharing import SharingUI


@pytest.mark.parametrize("download,data", [
    ("/home/test/Downloads", None),
    (r"C:\Users\test\Downloads", r"C:\Users\test\QQ"),
])
def test_storage_reads_only_paths_exposed_by_client(download, data):
    def field(cls, value):
        return SimpleNamespace(ClassName=cls, node=Node(children=(Node(type="TextControl", name=value),)))

    fields = [field("file-location-tips", download)]
    if data is not None:
        fields.append(field("location-tips", data))
    qq = SimpleNamespace(
        backend=SimpleNamespace(walk=lambda _: fields, snapshot=lambda n: n.node),
        control_ui=SimpleNamespace(by_class=lambda *_: object()),
    )
    page = NavigationUI(qq)
    page._settings_page = lambda _: nullcontext(object())
    assert page.storage_paths() == {"download_directory": download, "data_directory": data}
    fields[0] = field("file-location-tips", "/home/…")
    with pytest.raises(ControlUnavailable, match="complete absolute"):
        page.storage_paths()


@pytest.mark.parametrize("wrong_identity", [False, True])
def test_full_profile_waits_for_identity_field_and_still_checks_target(wrong_identity):
    peer = Target("30001", "测试好友", "private")
    panel, calls = object(), []
    seen = [0]
    identity = Node(children=(Node(type="TextControl", name="30002" if wrong_identity else peer.id),))

    def by_class(name, *_):
        if name == "contact-profile":
            return panel
        seen[0] += 1
        return identity if seen[0] > 1 else None

    backend = SimpleNamespace(
        main_window=object(), profile=lambda *_: None,
        invoke=lambda _: calls.append("open_profile"), snapshot=lambda n: n,
        walk=lambda _: [SimpleNamespace(ControlTypeName="TextControl", Name=peer.id)],
    )
    qq = SimpleNamespace(
        backend=backend, control_ui=SimpleNamespace(by_class=by_class),
        target=lambda _: peer, _open=lambda _: calls.append("chat"),
    )
    page = SharingUI(qq)
    page._clean = lambda: None
    page.profile_card = lambda _: nullcontext((object(), ()))
    page._button = lambda *_: object()
    if wrong_identity:
        with pytest.raises(TargetMismatch):
            page.profile(peer.id, full=True)
    else:
        assert page.profile(peer.id, full=True).id == peer.id
    assert seen[0] == 2
    assert calls == ["chat", "open_profile", "chat"]
