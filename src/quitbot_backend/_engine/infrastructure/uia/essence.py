"""Read rendered essence previews; never manufacture server identifiers or names."""

from ...domain.essence import EssenceItem, GroupEssences
from ...errors import ControlUnavailable, TargetMismatch
from ...models import text_of
from ...uia import wait_until


def parse_essences(node, group_id, name):
    titles = [text_of(n) for n in node.walk() if n.has_class("page-title")]
    if titles != [f"群精华-{name}"]:
        raise TargetMismatch("Essence window does not identify the expected group")
    rows = [n for n in node.walk() if n.has_class("message-item-container")]
    if not rows:
        if any(
            n.type == "TextControl" and n.name == "暂无精华消息" for n in node.walk()
        ):
            return GroupEssences(str(group_id), ())
        raise ControlUnavailable("Essence list has not loaded")
    result = []
    for row in rows:
        sender = [
            text_of(n)
            for n in row.walk()
            if n.has_class("message-item-container-top-info-nick")
        ]
        info = [n.name for n in row.children if n.type == "TextControl"]
        bodies = [n for n in row.children if n.has_class("short")]
        if len(sender) != 1 or len(info) != 5 or info[1:3] != ["发送", "·"]:
            raise ControlUnavailable("Unrecognized essence row layout")
        if len(bodies) == 1:
            kind = "text"
            preview = "".join(
                text_of(n) for n in bodies[0].children if not n.has_class("btn")
            )
        elif not bodies:
            images = [n for n in row.walk() if n.has_class("img_wrap")]
            names = [n for n in row.walk() if n.has_class("filename")]
            if (
                len(images) != 1
                or len(names) != 1
                or sum(n.has_class("play_circle_mask") for n in images[0].walk()) != 1
            ):
                raise ControlUnavailable("Unrecognized essence media preview")
            kind, preview = "video", text_of(names[0])
            if not preview:
                raise ControlUnavailable(
                    "Video essence filename preview has not loaded"
                )
        else:
            raise ControlUnavailable("Ambiguous essence text preview")
        result.append(
            EssenceItem(sender[0], info[0], info[4], info[3], preview, kind=kind)
        )
    return GroupEssences(str(group_id), tuple(result))


class EssenceUI:
    def __init__(self, chat):
        self.ui, self.b = chat, chat.b

    def read(self, key):
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Essences require a group")
            main = self.b.window
            title = f"群精华 - {target.name}"

            def windows():
                return [
                    w
                    for w in self.b.auto.GetRootControl().GetChildren()
                    if w.ProcessId == main.ProcessId
                    and w.Name == title
                    and w.ControlTypeName == "WindowControl"
                ]

            existing = windows()
            if len(existing) > 1:
                raise ControlUnavailable("Ambiguous essence windows")
            opened, window = not existing, existing[0] if existing else None
            try:
                if opened:
                    with self.ui.secondary.apps(target) as menu:
                        self.b.invoke(self.ui._menu_item(menu, "群精华"))
                    matches = wait_until(windows)
                    if len(matches) != 1:
                        raise ControlUnavailable("Ambiguous essence windows")
                    window = matches[0]

                def loaded():
                    try:
                        node = self.b.snapshot(window)
                        if not any(n.has_class("page-title") for n in node.walk()):
                            return None
                        return parse_essences(node, target.id, target.name)
                    except ControlUnavailable:
                        return None

                return wait_until(loaded)
            finally:
                if opened and window is not None:
                    window.GetWindowPattern().Close(waitTime=0.1)
