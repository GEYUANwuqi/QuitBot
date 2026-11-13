"""Native message actions with scope, state, and postcondition checks."""

from quitbot_backend._engine.domain.activities import system_text
from quitbot_backend._engine.domain.controls import MessageMutationReceipt
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    MutationOutcomeUnknown,
    TargetMismatch,
)
from quitbot_backend._engine.models import editor_has_content
from quitbot_backend._engine.ui.backend import wait_until

from .moderation import mutation_guard

ESSENCE_LABELS = {"设为精华": False, "移除精华": True}


def essence_state(actions):
    states = {
        ESSENCE_LABELS[a.name]
        for a in actions
        if a.enabled and a.name in ESSENCE_LABELS
    }
    if len(states) != 1:
        raise ControlUnavailable("Essence state/permission is not uniquely exposed")
    return states.pop()


class MessageMutationsUI:
    def __init__(self, client):
        self.client, self.ui, self.b = client, client.control_ui, client.backend

    def essence_state(self, key, message_id):
        with self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Essence actions require a group")
            return essence_state(self.ui.message_actions(key, message_id))

    def set_essence(self, key, message_id, enabled):
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        submitted = [False]
        with mutation_guard(submitted), self.ui.checked(key) as target:
            if target.kind != "group":
                raise ValueError("Essence actions require a group")
            menu = self.ui.message_menu(key, message_id)
            try:
                actions = self.ui._actions(menu)
                before = essence_state(actions)
                if before != enabled:
                    labels = [
                        a.name
                        for a in actions
                        if a.enabled
                        and ESSENCE_LABELS.get(a.name) is before
                        and a.name in ESSENCE_LABELS
                    ]
                    if len(labels) != 1:
                        raise ControlUnavailable("Unique essence action not found")
                    leaf = self.ui._menu_item(menu, labels[0])
                    try:
                        submitted[0] = True
                        self.b.invoke(leaf)
                        if self.ui.by_class("q-dialog-main") is not None:
                            raise ControlUnavailable(
                                "Unexpected essence confirmation retained"
                            )
                    except Exception as error:
                        raise MutationOutcomeUnknown(
                            "Essence submission outcome unknown; inspect before retrying"
                        ) from error
            finally:
                self.ui._dismiss_menu()
            try:
                after = wait_until(
                    lambda: self.essence_state(key, message_id),
                    lambda value: value is enabled,
                )
            except Exception as error:
                if before == enabled:
                    raise
                raise MutationOutcomeUnknown(
                    "Essence state not confirmed after submission"
                ) from error
            return MessageMutationReceipt(
                target.id, str(message_id), "essence", before, after
            )

    def recall(self, key, message_id, *, expected_text=None):
        if expected_text is not None and not isinstance(expected_text, str):
            raise TypeError("expected_text must be a string or None")
        submitted = [False]
        with mutation_guard(submitted), self.ui.checked(key) as target:
            message = self.client.get_message(target, message_id)
            if message.direction != "outgoing":
                raise ControlUnavailable(
                    "Recall currently supports the account's own loaded messages only"
                )
            if expected_text is not None and message.text != expected_text:
                raise TargetMismatch("Recall text does not match the expected message")
            if editor_has_content(self.b.snapshot(self.b.editor())):
                raise DraftNotEmpty("Existing draft retained before recall")
            before = self.client._read(target)
            seen = {(m.id, m.text) for m in before}
            menu = self.ui.message_menu(target, message_id)
            try:
                leaf = self.ui._menu_item(menu, "撤回")
                if not leaf.IsEnabled:
                    raise ControlUnavailable("Recall is disabled for this message")
                try:
                    submitted[0] = True
                    self.b.invoke(leaf)
                    if self.ui.by_class("q-dialog-main") is not None:
                        raise ControlUnavailable(
                            "Unexpected recall confirmation retained"
                        )

                    def changed():
                        rows = self.client._read(target)
                        notices = [
                            m
                            for m in rows
                            if m.id == str(message_id)
                            and m.direction == "system"
                            and m.kind == "system"
                            and system_text(m).removesuffix("重新编辑").strip()
                            == "你撤回了一条消息"
                            and (m.id, m.text) not in seen
                        ]
                        return notices[-1].text if len(notices) == 1 else None

                    notice = wait_until(changed, timeout=5)
                except Exception as error:
                    raise MutationOutcomeUnknown(
                        "Recall outcome not confirmed; inspect before retrying"
                    ) from error
            finally:
                self.ui._dismiss_menu()
            return MessageMutationReceipt(
                target.id, str(message_id), "recall", "present", "recalled", notice
            )
