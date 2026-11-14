"""Copy selected message images using QQ's own clipboard export."""

import os
import stat
from pathlib import Path

from ...errors import ControlUnavailable
from ...infrastructure.storage.media import MAX_IMAGE_BYTES, image_extension
from ...platforms import clipboard_sequence
from ..backend import wait_until


def read_image_file(path):
    source = Path(path).resolve(strict=True)
    if not source.is_relative_to(Path.home().resolve()):
        raise ControlUnavailable("QQ 图片资源不在当前用户目录内")
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
            raise ControlUnavailable("QQ 图片资源类型或大小异常")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ControlUnavailable("QQ 图片资源不属于当前用户")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if len(data) != info.st_size or len(data) > MAX_IMAGE_BYTES:
            raise ControlUnavailable("QQ 图片资源仍在变化，请稍后重新读取")
        image_extension(data)
        return data
    finally:
        os.close(fd)


def export_images(client, target, message_id):
    ui, backend = client.control_ui, client.backend
    with ui.checked(target):
        message = client.get_message(target, message_id)
        expected = sum(a.kind == "image" for a in message.attachments)
        if not expected or expected != len(message.attachments) or expected > 8:
            raise ControlUnavailable("当前消息没有可完整导出的图片附件")
        before = clipboard_sequence(backend.auto)
        menu = ui.message_menu(target, message_id)
        try:
            copies = [
                c
                for c in backend.walk(menu)
                if c.ControlTypeName == "MenuItemControl" and c.Name == "复制"
            ]
            if len(copies) != 1:
                raise ControlUnavailable("当前消息没有唯一的复制入口")
            backend.invoke(copies[0])
            wait_until(lambda: clipboard_sequence(backend.auto) != before, timeout=3)
            paths = backend.auto.copied_files()
            if len(paths) != expected:
                raise ControlUnavailable("QQ 复制的图片数量与所选消息不一致")
            images = [read_image_file(path) for path in paths]
            if sum(map(len, images)) > MAX_IMAGE_BYTES:
                raise ControlUnavailable("单条消息图片总量超过 32 MiB")
            return images
        finally:
            ui._dismiss_menu()


def download_message_file(client, target, message_id, destination, *, expected_name):
    """Save an exact file bubble through its native Save As dialog."""
    import time

    from ...errors import TargetMismatch
    from .group_files import downloaded_file_state, file_name
    from .moderation import mutation_guard

    name = file_name(expected_name)
    requested = Path(destination).expanduser()
    file_name(requested.name)
    if requested.exists() or requested.is_symlink():
        raise FileExistsError("下载目标已存在")
    path = requested.parent.resolve(strict=True) / requested.name
    if not path.parent.is_dir() or path.suffix.lower() != Path(name).suffix.lower():
        raise ValueError("请使用已有目录并保留文件扩展名")
    ui, backend = client.control_ui, client.backend
    submitted = [False]
    with ui.checked(target), mutation_guard(submitted):
        message = client.get_message(target, message_id)
        if (
            len(message.attachments) != 1
            or message.attachments[0].kind != "file"
            or message.attachments[0].name != name
        ):
            raise TargetMismatch("所选消息的文件名与 expected_name 不一致")
        menu = ui.message_menu(target, message_id)
        picker = None
        try:
            entries = [
                c
                for c in backend.walk(menu)
                if c.ControlTypeName == "MenuItemControl" and c.Name == "另存为"
            ]
            if len(entries) != 1:
                raise ControlUnavailable("所选文件消息没有另存为入口")
            backend.invoke(entries[0])
            picker = wait_until(
                lambda: backend.auto.file_picker(backend, backend.window)
            )
            picker.set_path(path, expected_name=name)
            if path.exists() or path.is_symlink():
                raise FileExistsError("确认前下载目标已出现")
            submitted[0] = True
            picker.confirm()
            wait_until(
                lambda: backend.auto.file_picker(backend, backend.window),
                lambda value: value is None,
            )
            previous = [None, 0.0]

            def settled():
                if path.is_symlink():
                    raise ValueError("下载目标路径异常")
                state = downloaded_file_state(path)
                if state is None:
                    return None
                if state != previous[0]:
                    previous[:] = [state, time.monotonic()]
                    return None
                return state if time.monotonic() - previous[1] >= 1 else None

            size, digest = wait_until(settled, timeout=45)
            return {
                "target_id": client.target(target).id,
                "ui_message_id": message.id,
                "name": name,
                "local_path": str(path),
                "size_bytes": size,
                "sha256": digest,
                "verified_in_ui": True,
            }
        finally:
            if picker is not None and not submitted[0]:
                picker.cancel()
            ui._dismiss_menu()


def face_ids_from_rich_text(value, expected_count):
    import re
    from xml.etree import ElementTree

    if len(value) > 65536 or re.search(
        r"<!\s*(?:DOCTYPE|ENTITY)\b", value, re.IGNORECASE
    ):
        raise ControlUnavailable("QQ 富文本格式不符合预期")
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError as error:
        raise ControlUnavailable("QQ 富文本解析失败") from error
    if root.tag != "QQRichEditFormat":
        raise ControlUnavailable("QQ 富文本类型不符合预期")
    result = []
    for element in root.findall("EditElement"):
        if element.get("type") != "2":
            continue
        value = element.get("sysfaceindex", "")
        if not re.fullmatch(r"\d{1,6}", value):
            raise ControlUnavailable("QQ 表情编号缺失或异常")
        result.append(int(value))
    if not 0 < len(result) == expected_count <= 128:
        raise ControlUnavailable("QQ 表情数量与所选消息不一致")
    return result


def export_face_ids(client, target, message_id):
    ui, backend = client.control_ui, client.backend
    with ui.checked(target):
        message = client.get_message(target, message_id)
        if not message.face_count or message.kind == "random_sticker":
            raise ControlUnavailable("所选消息没有普通 QQ 表情")
        before = clipboard_sequence(backend.auto)
        menu = ui.message_menu(target, message_id)
        try:
            entries = [
                c
                for c in backend.walk(menu)
                if c.ControlTypeName == "MenuItemControl" and c.Name == "复制"
            ]
            if len(entries) != 1:
                raise ControlUnavailable("所选消息没有唯一的复制入口")
            backend.invoke(entries[0])
            wait_until(lambda: clipboard_sequence(backend.auto) != before, timeout=3)
            return face_ids_from_rich_text(
                backend.auto.copied_rich_text(), message.face_count
            )
        finally:
            ui._dismiss_menu()
