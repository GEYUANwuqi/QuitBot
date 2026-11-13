import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from uia_fixtures import mock_context_menu_interface

from quitbot_backend._engine.domain.group_files import GroupFileEntry
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import Node
from quitbot_backend._engine.ui.workflows.group_files import (
    GroupFilesUI,
    downloaded_file_state,
    parse_file_entry,
    require_loaded_root,
    unique_file_entry,
)


def text(value):
    return Node(type="TextControl", name=value)


def row(name, details, uploader="来自 测试账号"):
    return Node(
        class_name="file-container",
        children=(
            Node(class_name="file-name", children=(text(name),)),
            Node(class_name="file-details-info", children=(text(details),)),
            Node(class_name="file-send-desc", children=(text(uploader),)),
            text("11:37"),
        ),
    )


def test_file_identity_uses_observed_count_or_size_and_never_extension_or_server_id():
    folder = parse_file_entry(row("something.txt", "0\u2009个文件", "更新 测试账号"))
    file = parse_file_entry(row("no-extension", "47.0\u2009B"))
    unknown = parse_file_entry(row("unknown.txt", "处理中"))
    assert folder.kind == "folder" and folder.count == 0
    assert folder.size_text is None and folder.id is None
    assert file.kind == "file" and file.date_text == "11:37"
    assert file.id is None and unknown.kind == "unknown"


def test_duplicate_or_changed_file_identity_is_rejected_before_mutation():
    old = GroupFileEntry("one.txt", "file", "47.0 B", uploader_text="来自 测试账号")
    with pytest.raises(ControlUnavailable):
        unique_file_entry([old, old], "one.txt")
    with pytest.raises(TargetMismatch):
        unique_file_entry([old], "one.txt", kind="folder")
    changed = GroupFileEntry("one.txt", "file", "48.0 B", uploader_text="来自 测试账号")
    with pytest.raises(TargetMismatch):
        unique_file_entry([changed], "one.txt", expected=old)
    assert unique_file_entry([old], "one.txt", expected=old) == old


@pytest.mark.parametrize("defunct", [False, True])
def test_file_rows_reacquired_after_transient_tree_rebuild_and_empty_is_valid(defunct):
    ui = GroupFilesUI.__new__(GroupFilesUI)
    old, fresh = object(), object()
    calls = []

    class Stale(Exception):
        hresult = -2147220991

    def rows(_):
        calls.append("read")
        return [old if len(calls) == 1 else fresh]

    def snapshot(control):
        if control is old:
            if defunct:
                return Node()
            raise Stale()
        return row("one.txt", "47.0 B")

    ui._rows = rows
    ui.b = SimpleNamespace(snapshot=snapshot)
    assert ui._entries(None)[0].name == "one.txt"
    assert calls == ["read", "read"]
    ui._rows = lambda _: []
    assert ui._entries(None) == ()


@pytest.mark.parametrize("source,destination", [(None, None), ("folder", "folder")])
def test_move_same_location_rejected_before_ui(source, destination):
    with pytest.raises(ValueError):
        GroupFilesUI.__new__(GroupFilesUI).move_file(
            "20001", "one.txt", destination, source_folder=source
        )


@pytest.mark.parametrize("kind,duplicate", [("file", False), ("folder", True)])
def test_move_ambiguous_or_nonfolder_destination_does_not_open_menu(kind, duplicate):
    ui = GroupFilesUI.__new__(GroupFilesUI)

    @contextmanager
    def view(_):
        yield SimpleNamespace(id="20001", name="测试群"), None

    entry = GroupFileEntry("destination", kind)
    ui.secondary = SimpleNamespace(files_view=view)
    ui._require_clean = lambda *_: None
    ui._entries = lambda _: [entry, entry] if duplicate else [entry]
    with pytest.raises(ControlUnavailable if duplicate else TargetMismatch):
        ui.move_file("20001", "one.txt", "destination")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "..",
        "a ",
        "x.",
        "a/b",
        "a\n",
        "NUL.txt",
        "a" * 201,
    ],
)
def test_invalid_file_names_fail_before_any_uia_access(value):
    with pytest.raises(ValueError):
        GroupFilesUI.__new__(GroupFilesUI).create_folder("20001", value)


def test_root_header_guard_rejects_unknown_breadcrumbs_or_multiple_headers():
    header = Node(
        class_name="file-list-header-container file-list-header",
        children=(Node(class_name="file-list-header-container-pull-down-group"),),
    )
    area = Node(
        class_name="file-area-container",
        children=(Node(class_name="text-ellipsis", children=(text("群文件 - 测试群"),)),),
    )
    search = Node(type="EditControl", name="搜索本群文件")
    require_loaded_root(Node(children=(area, search, header)), "测试群")
    with pytest.raises(ControlUnavailable):
        require_loaded_root(Node(children=(area, search, header, header)), "测试群")
    with pytest.raises(ControlUnavailable):
        require_loaded_root(
            Node(
                children=(
                    area,
                    search,
                    Node(
                        class_name=header.class_name,
                        children=(*header.children, text("sample-folder")),
                    ),
                )
            ),
            "测试群",
        )
    with pytest.raises(ControlUnavailable):
        require_loaded_root(Node(children=(area, header)), "测试群")
    with pytest.raises(ControlUnavailable):
        require_loaded_root(Node(children=(area, search, header)), "other")


def test_root_read_does_not_require_management_buttons_for_ordinary_member(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "quitbot_backend._engine.ui.workflows.group_files.require_loaded_root",
        lambda node, name: seen.append(name),
    )
    ui = GroupFilesUI.__new__(GroupFilesUI)
    ui.ui = SimpleNamespace(by_class=lambda *_: None)
    ui._picker = lambda _: None
    ui.b = SimpleNamespace(snapshot=lambda _: Node())
    ui._rows = lambda _: [object()]

    def forbidden(*_, **__):
        raise AssertionError("A read must not require a write permission button")

    ui._button = forbidden
    ui._require_clean(None, SimpleNamespace(name="测试群"))
    assert seen == ["测试群"]


def create_fixture(*, title="新建文件夹", read_error=None):
    ui = GroupFilesUI.__new__(GroupFilesUI)
    window = object()
    state = {"dialog": None, "calls": []}
    node = Node(
        children=(
            Node(class_name="q-dialog-window-title", children=(text(title),)),
            text("请输入文件夹名称"),
        )
    )
    value = SimpleNamespace(Value="")
    value.SetValue = lambda s, **_: setattr(value, "Value", s)
    edit = SimpleNamespace(
        ControlTypeName="EditControl",
        ClassName="q-input__inner has-border",
        GetValuePattern=lambda: value,
    )
    dialog = SimpleNamespace()

    @contextmanager
    def files_view(_):
        yield SimpleNamespace(id="20001", name="测试群"), window

    def invoke(name):
        state["calls"].append(name)
        if name == "新建文件夹":
            state["dialog"] = dialog
        elif name in ("确定", "取消"):
            state["dialog"] = None

    def await_entry(*_, **__):
        if read_error:
            raise read_error
        return GroupFileEntry(value.Value, "folder", count=0)

    ui.secondary = SimpleNamespace(files_view=files_view)
    ui.b = SimpleNamespace(
        invoke=invoke, snapshot=lambda _: node, walk=lambda _: [edit]
    )
    ui._require_clean = lambda *_: None
    ui._require_absent = lambda *_: None
    ui._button = lambda _, name: name
    ui._dialog = lambda _: state["dialog"]
    ui._await_entry = await_entry
    return ui, state


def test_create_folder_only_confirms_recognized_dialog_and_returns_verified_entry():
    ui, state = create_fixture()
    receipt = ui.create_folder("20001", "测试-folder.txt")
    assert state["calls"] == ["新建文件夹", "确定"]
    assert receipt.after == GroupFileEntry("测试-folder.txt", "folder", count=0)
    assert receipt.before is None and receipt.verified_in_ui


def test_unrecognized_dialog_is_cancelled_without_submission():
    ui, state = create_fixture(title="Unexpected dialog")
    with pytest.raises(ControlUnavailable):
        ui.create_folder("20001", "sample-folder")
    assert state["calls"] == ["新建文件夹", "取消"]


def test_post_submit_failure_is_outcome_unknown_and_never_retried():
    ui, state = create_fixture(read_error=TimeoutError("row did not appear"))
    with pytest.raises(MutationOutcomeUnknown):
        ui.create_folder("20001", "sample-folder")
    assert state["calls"] == ["新建文件夹", "确定"]


def test_casefold_existing_name_prevents_ambiguous_creation():
    ui = GroupFilesUI.__new__(GroupFilesUI)
    ui._entries = lambda _: [GroupFileEntry("Test.txt", "file")]
    with pytest.raises(ControlUnavailable):
        ui._require_absent(None, "test.TXT")


def test_upload_requires_regular_existing_file_before_uia(tmp_path):
    ui = GroupFilesUI.__new__(GroupFilesUI)
    with pytest.raises(FileNotFoundError):
        ui.upload_file("20001", tmp_path / "missing.txt")
    with pytest.raises(ValueError):
        ui.upload_file("20001", tmp_path)


def file_mutation_fixture(*, original_value="one", confirmation_name="one.txt"):
    ui, state = create_fixture()
    before = GroupFileEntry("one.txt", "file", "47.0 B", uploader_text="来自 测试账号")
    after = GroupFileEntry("two.txt", "file", "47.0 B", uploader_text="来自 测试账号")
    value = SimpleNamespace(Value=original_value)
    value.SetValue = lambda s, **_: setattr(value, "Value", s)
    dialog = SimpleNamespace()
    state["entries"] = [before]

    @contextmanager
    def menu(*_, **__):
        yield "file-menu", before

    def named_dialog(_, title, body):
        if title == "提示" and body != f"你确定要删除文件{confirmation_name}吗？":
            raise ControlUnavailable("Wrong confirmation identity")
        return dialog

    def invoke(item):
        label = item if isinstance(item, str) else item.Name
        state["calls"].append(label)
        if label in ("重命名", "删除"):
            state["dialog"] = dialog
            state["action"] = label
        elif label == "确定":
            state["dialog"] = None
            state["entries"] = [after] if state["action"] == "重命名" else []
        elif label == "取消":
            state["dialog"] = None

    ui.ui = SimpleNamespace(
        _menu_item=lambda _, label: SimpleNamespace(Name=label, IsEnabled=True)
    )
    ui._file_menu = menu
    ui._named_dialog = named_dialog
    ui._name_input = lambda _: value
    ui._entries = lambda _: state["entries"]
    ui._await_entry = lambda *_: after
    ui.b.invoke = invoke
    return ui, state


def test_rename_refuses_a_changed_original_value_and_cancels_without_committing():
    ui, state = file_mutation_fixture(original_value="other.txt")
    with pytest.raises(TargetMismatch):
        ui.rename_file("20001", "one.txt", "two.txt")
    assert state["calls"] == ["重命名", "取消"]


def test_rename_preserves_the_extension_without_opening_any_window():
    with pytest.raises(ValueError):
        GroupFilesUI.__new__(GroupFilesUI).rename_file(
            "20001", "one.txt", "two.exe"
        )


def test_rename_checks_new_metadata_and_old_name_absence():
    ui, state = file_mutation_fixture()
    receipt = ui.rename_file("20001", "one.txt", "two.txt")
    assert state["calls"] == ["重命名", "确定"]
    assert receipt.before.name == "one.txt" and receipt.after.name == "two.txt"
    ui, state = file_mutation_fixture()
    ui._await_entry = lambda *_: GroupFileEntry("two.txt", "file", "999 B")
    with pytest.raises(MutationOutcomeUnknown):
        ui.rename_file("20001", "one.txt", "two.txt")
    assert state["calls"] == ["重命名", "确定"]


def test_delete_only_confirms_exact_requested_filename_and_checks_absence():
    ui, state = file_mutation_fixture(confirmation_name="other.txt")
    with pytest.raises(ControlUnavailable):
        ui.delete_file("20001", "one.txt")
    assert state["calls"] == ["删除", "取消"]
    ui, state = file_mutation_fixture()
    receipt = ui.delete_file("20001", "one.txt")
    assert state["calls"] == ["删除", "确定"]
    assert receipt.before.name == "one.txt" and receipt.after is None


def test_batch_delete_cancels_if_selection_changes_while_confirmation_is_open(monkeypatch):
    """A matching count in the dialog must not authorize a changed file set."""
    mock_context_menu_interface(monkeypatch)
    ui, state = file_mutation_fixture()
    entries = [GroupFileEntry(name, "file", "47.0 B", uploader_text="来自 测试账号")
               for name in ("one.txt", "two.txt", "foreign.txt")]
    toggles = [SimpleNamespace(ToggleState=0) for _ in entries]
    for toggle in toggles:
        toggle.Toggle = lambda toggle=toggle, **_: setattr(toggle, "ToggleState", 1)
    rows = [SimpleNamespace(
        checkbox=SimpleNamespace(ControlTypeName="CheckBoxControl",
                                 GetTogglePattern=lambda toggle=toggle: toggle),
        Element=SimpleNamespace(QueryInterface=lambda _: SimpleNamespace(ShowContextMenu=lambda: None)),
    ) for toggle in toggles]
    toolbar = object()
    leaf = SimpleNamespace(Name="删除", ControlTypeName="ListItemControl")
    icon = SimpleNamespace(Name="删除", ClassName="op-icon", IsEnabled=True, IsOffscreen=False)
    state["multi"] = False
    ui._fresh_view = ui.secondary.files_view
    ui._read_rows = lambda _: (rows, entries)
    ui.b.walk = lambda root: [leaf] if root is toolbar else [root.checkbox]
    ui.b.find = lambda root, predicate: next(item for item in ui.b.walk(root) if predicate(item))
    ui.ui.navigation = SimpleNamespace(_menu=lambda: "menu")
    ui.ui._dismiss_menu = lambda: None
    ui.ui.by_class = lambda name, *_, **__: (
        toolbar if name == "forward-ops" and state["multi"] else
        icon if name == "op-icon" else "close-selection" if name == "close-btn" else None)

    def invoke(item):
        name = item if isinstance(item, str) else item.Name
        state["calls"].append(name)
        if name == "多选":
            state["multi"], toggles[0].ToggleState = True, 1
        elif name == "删除":
            state["dialog"] = object()
        elif name == "取消":
            state["dialog"] = None
        elif name == "close-selection":
            state["multi"] = False

    def changed_selection(*_):
        toggles[1].ToggleState, toggles[2].ToggleState = 0, 1
        return state["dialog"]

    ui.b.invoke = invoke
    ui._named_dialog = changed_selection
    with pytest.raises(TargetMismatch, match="selection changed"):
        ui.delete_files("20001", ["one.txt", "two.txt"])
    assert state["calls"] == ["多选", "删除", "取消", "close-selection"]
    assert state["dialog"] is None and not state["multi"]


def test_delete_dialog_title_class_differs_from_rename_but_exact_body_is_required():
    ui = GroupFilesUI.__new__(GroupFilesUI)
    ui._dialog = lambda _: "dialog"
    ui.b = SimpleNamespace(
        snapshot=lambda _: Node(
            children=(
                Node(class_name="q-dialog-title", children=(text("提示"),)),
                Node(
                    class_name="q-dialog-content",
                    children=(text("你确定要删除文件one.txt吗？"),),
                ),
            )
        )
    )
    assert ui._named_dialog(None, "提示", "你确定要删除文件one.txt吗？") == "dialog"
    with pytest.raises(ControlUnavailable):
        ui._named_dialog(None, "提示", "你确定要删除文件other.txt吗？")


def upload_fixture(*, retain_path=True, read_error=None):
    ui, state = create_fixture()
    picker = SimpleNamespace(Name="请选择", NativeWindowHandle=501, ControlTypeName="WindowControl", ClassName="#32770")
    value = SimpleNamespace(Value="")
    value.SetValue = lambda s, **_: setattr(
        value, "Value", s if retain_path else "wrong.txt"
    )
    edit = SimpleNamespace(
        ControlTypeName="EditControl",
        ClassName="Edit",
        AutomationId="1148",
        GetValuePattern=lambda: value,
    )
    confirm = SimpleNamespace(
        ControlTypeName="ButtonControl",
        ClassName="Button",
        AutomationId="1",
        Name="确认",
    )
    cancel = SimpleNamespace(
        ControlTypeName="ButtonControl",
        ClassName="Button",
        AutomationId="2",
        Name="取消",
    )
    controls = [edit, confirm, cancel]

    def invoke(item):
        label = item if isinstance(item, str) else item.Name
        state["calls"].append(label)
        if label == "上传文件":
            state["picker"] = picker
        elif label in ("确认", "取消"):
            state["picker"] = None

    def await_entry(_, name, *__, **___):
        if read_error:
            raise read_error
        return GroupFileEntry(name, "file", "47.0 B")

    ui.b.invoke = invoke
    from quitbot_backend._engine.platforms.windows.file_picker import find_file_picker

    ui.b.walk = lambda root: controls if root is picker else ([picker] if state.get("picker") else [])
    ui.b.find = lambda _, predicate: next(c for c in controls if predicate(c))
    ui._picker = lambda owner: find_file_picker(ui.b, owner)
    ui._await_entry = await_entry
    return ui, state


def test_upload_picker_confirmation_is_the_single_commit_without_chat_preview(tmp_path):
    path = tmp_path / "sample-upload.txt"
    path.write_text("test", encoding="utf-8")
    ui, state = upload_fixture()
    receipt = ui.upload_file("20001", path)
    assert state["calls"] == ["上传文件", "确认"]
    assert receipt.after.name == path.name and receipt.local_path == str(path.resolve())


def test_upload_bad_path_is_cancelled_and_post_commit_read_failure_is_uncertain(
    tmp_path,
):
    path = tmp_path / "sample-upload.txt"
    path.write_text("test", encoding="utf-8")
    ui, state = upload_fixture(retain_path=False)
    with pytest.raises(TargetMismatch):
        ui.upload_file("20001", path)
    assert state["calls"] == ["上传文件", "取消"]
    ui, state = upload_fixture(read_error=TimeoutError("file row did not load"))
    with pytest.raises(MutationOutcomeUnknown):
        ui.upload_file("20001", path)
    assert state["calls"] == ["上传文件", "确认"]


def test_download_refuses_existing_destination_before_ui_and_hashes_completed_file(
    tmp_path,
):
    path = tmp_path / "out.txt"
    path.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        GroupFilesUI.__new__(GroupFilesUI).download_file("20001", "one.txt", path)
    assert path.read_bytes() == b"original"
    assert downloaded_file_state(path, "8.0\u2009B") == (
        8,
        hashlib.sha256(b"original").hexdigest(),
    )
    assert downloaded_file_state(path, "47.0 B") is None
    assert downloaded_file_state(tmp_path / "missing.txt") is None


def test_native_save_as_verifies_source_then_writes_one_new_local_destination(tmp_path):
    ui, state = file_mutation_fixture()
    content = b"x" * 47
    value = SimpleNamespace(Value="one.txt")
    value.SetValue = lambda text, **_: setattr(value, "Value", text)
    edit = SimpleNamespace(
        ControlTypeName="EditControl",
        AutomationId="1001",
        GetValuePattern=lambda: value,
    )
    confirm = SimpleNamespace(
        ControlTypeName="ButtonControl",
        AutomationId="1",
        ClassName="Button",
        Name="另存为",
    )
    cancel = SimpleNamespace(
        ControlTypeName="ButtonControl",
        AutomationId="2",
        ClassName="Button",
        Name="取消",
    )
    picker = SimpleNamespace(Name="另存为", NativeWindowHandle=501, ControlTypeName="WindowControl", ClassName="#32770")
    controls = [edit, confirm, cancel]

    def invoke(control):
        state["calls"].append(control.Name)
        if control is confirm:
            Path(value.Value).write_bytes(content)
            state["picker"] = None
        else:
            state["picker"] = picker

    ui.b.invoke = invoke
    from quitbot_backend._engine.platforms.windows.file_picker import find_file_picker

    ui.b.walk = lambda root: controls if root is picker else ([picker] if state.get("picker") else [])
    ui.b.find = lambda _, predicate: next(c for c in controls if predicate(c))
    ui._picker = lambda owner: find_file_picker(ui.b, owner)
    path = tmp_path / "download.txt"
    receipt = ui.download_file("20001", "one.txt", path)
    assert state["calls"] == ["另存为", "另存为"]
    assert (
        receipt.size_bytes == 47
        and receipt.sha256 == hashlib.sha256(content).hexdigest()
    )
    assert receipt.local_path == str(path.resolve()) and path.read_bytes() == content
