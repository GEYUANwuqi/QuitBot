"""Contact notification pages exposed by native QQ accessibility controls."""

import re

from quitbot_backend._engine.domain.activities import parse_system_cards
from quitbot_backend._engine.errors import ControlUnavailable, MutationOutcomeUnknown
from quitbot_backend._engine.locking import ui_transaction
from quitbot_backend._engine.uia import wait_until

from .inspection import require_unlocked


class NotificationUI:
    def __init__(self, client):
        self.b = client.backend

    def page(self, kind):
        if kind not in ("group", "friend"):
            raise ValueError("Notification kind must be group or friend")
        b = self.b
        require_unlocked(b.auto)
        b.navigate("联系人")

        def heading():
            return b.find(
                b.window,
                lambda c: (
                    "contact-notify__header" in c.ClassName.split()
                    and not c.IsOffscreen
                    and any(
                        x.ControlTypeName == "TextControl"
                        and x.Name == ("新群聊" if kind == "group" else "新朋友")
                        for x, _ in b.auto.WalkControl(c, maxDepth=3)
                    )
                ),
                False,
            )

        current = heading()
        if current is None:
            entry = b.find(
                b.window,
                lambda c: (
                    c.ControlTypeName == "ButtonControl"
                    and re.fullmatch(
                        ("新群聊" if kind == "group" else "新朋友") + r"(?: \d+\+?)?",
                        c.Name,
                    )
                ),
            )
            b.focus_control(entry)
            b.auto.SendKeys("{Enter}", waitTime=0.1)
            current = wait_until(heading)
        siblings = [
            c
            for c in current.GetParentControl().GetChildren()
            if "q-scroll-view" in c.ClassName.split() and not c.IsOffscreen
        ]
        if len(siblings) != 1:
            raise ControlUnavailable("Unrecognized notification list container")
        return siblings[0]

    def read(self, kind):
        b = self.b
        b.check_thread()
        with ui_transaction():
            previous = b.window
            try:
                return parse_system_cards(b.snapshot(self.page(kind)), kind)
            finally:
                b.window = previous

    def respond(self, kind, ui_key, approve):
        if (
            not isinstance(approve, bool)
            or not isinstance(ui_key, str)
            or not ui_key.startswith("uia:")
        ):
            raise ValueError("Expected a local UI request key and a boolean decision")
        b = self.b
        b.check_thread()
        with ui_transaction():
            page = self.page(kind)
            matches = []
            for row in b.walk(page):
                if "list-item" not in row.ClassName.split():
                    continue
                cards = parse_system_cards(b.snapshot(row), kind)
                if len(cards) == 1 and cards[0].ui_key == ui_key:
                    matches.append((row, cards[0]))
            if len(matches) != 1 or matches[0][1].status != "pending":
                raise ControlUnavailable(
                    "Request is stale, ambiguous, or no longer pending"
                )
            row, card = matches[0]
            label = "同意" if approve else "拒绝"
            if label not in card.actions:
                raise ControlUnavailable("Requested decision is not exposed by QQ")
            if kind == "friend" and not approve:
                arrow = b.find(
                    row,
                    lambda c: "q-select-button-action__wrapper" in c.ClassName.split(),
                )
                b.invoke(arrow)
                menu = wait_until(
                    lambda: b.find(
                        b.window,
                        lambda c: (
                            c.ControlTypeName == "MenuControl"
                            and "q-select-button-options" in c.ClassName.split()
                        ),
                        False,
                    )
                )
                leaf = b.find(
                    menu,
                    lambda c: (
                        c.ControlTypeName == "MenuItemControl" and c.Name == "拒绝"
                    ),
                )
            else:
                leaf = b.find(
                    row,
                    lambda c: (
                        (c.ControlTypeName == "ButtonControl" and c.Name == label)
                        or (
                            "q-selected-area" in c.ClassName.split()
                            and any(
                                x.ControlTypeName == "TextControl" and x.Name == label
                                for x, _ in b.auto.WalkControl(c, maxDepth=2)
                            )
                        )
                    ),
                )
            if not leaf.IsEnabled:
                raise ControlUnavailable("Request decision control is disabled")
            try:
                b.invoke(leaf)

                def changed():
                    candidates = [c for c in self.read(kind) if c.ui_key == ui_key]
                    return (
                        candidates[0]
                        if len(candidates) == 1
                        and candidates[0].status
                        in (
                            ("已同意", "已添加", "已通过")
                            if approve
                            else ("已拒绝", "已忽略")
                        )
                        else None
                    )

                after = wait_until(changed)
            except Exception as error:
                raise MutationOutcomeUnknown(
                    "Request decision submitted; inspect QQ before retrying"
                ) from error
            return {
                "ui_key": ui_key,
                "kind": kind,
                "approve": approve,
                "status": after.status,
                "verified_in_ui": True,
            }
