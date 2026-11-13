"""Native quick/palette reactions; chip labels and protocol IDs remain unknown."""

import time

from quitbot_backend._engine.domain.reactions import (
    MessageReactions,
    ReactionChip,
    ReactionReceipt,
)
from quitbot_backend._engine.errors import (
    ControlUnavailable,
    DraftNotEmpty,
    TargetMismatch,
)
from quitbot_backend._engine.models import editor_has_content, text_of
from quitbot_backend._engine.ui.backend import wait_until

from .inspection import hover_control
from .moderation import mutation_guard
from .secondary import more_reaction_control

QUICK_LABELS = frozenset({"赞", "OK", "爱心", "汪汪", "敬礼", "鼓掌"})


def parse_reaction_chip(node, *, label=None):
    """Read only the observed chip count and account-local selected classes."""
    if not node.has_class("reaction-item"):
        raise ControlUnavailable("Expected an actual reaction chip")
    backgrounds = [n for n in node.walk() if n.has_class("reaction-item-bg")]
    if len(backgrounds) != 1:
        raise ControlUnavailable("Reaction chip selection state is ambiguous")
    selected = node.has_class("reaction-item--clicked")
    if backgrounds[0].has_class("reaction-item-bg--clicked") != selected:
        raise ControlUnavailable("Reaction chip selection classes disagree")
    counts = [
        text_of(n).strip() for n in node.walk() if n.has_class("reaction-item-text")
    ]
    count = (
        int(counts[0])
        if len(counts) == 1 and counts[0].isascii() and counts[0].isdigit()
        else None
    )
    return ReactionChip(label, count, selected)


class ReactionsUI:
    def __init__(self, client):
        self.client, self.ui, self.b = client, client.control_ui, client.backend

    def _row(self, message_id):
        rows = [
            c
            for c in self.b.walk(self.b.window)
            if c.AutomationId == str(message_id) and "ml-item" in c.ClassName.split()
        ]
        if len(rows) != 1:
            raise ControlUnavailable("Expected one loaded message row for reactions")
        return rows[0]

    def _chips(self, message_id):
        return [
            c
            for c in self.b.walk(self._row(message_id))
            if "reaction-item" in c.ClassName.split()
        ]

    def _states(self, message_id):
        return tuple(
            parse_reaction_chip(self.b.snapshot(c)) for c in self._chips(message_id)
        )

    def _validate(self, target, message_id, expected_text, *, writing=False):
        if target.kind != "group":
            raise ValueError("Native reactions currently require a group")
        message = self.client.get_message(target, message_id)
        if message.direction == "system":
            raise ControlUnavailable("System rows cannot receive message reactions")
        if expected_text is not None and message.text != expected_text:
            raise TargetMismatch("Reaction message does not match expected text")
        if writing and editor_has_content(self.b.snapshot(self.b.editor())):
            raise DraftNotEmpty("Existing draft retained before reaction mutation")

    def reactions(self, key, message_id):
        with self.ui.checked(key) as target:
            self._validate(target, message_id, None)
            return MessageReactions(
                target.id, str(message_id), self._states(message_id)
            )

    def _quick_submit(self, menu, label, submitted):
        items = wait_until(
            lambda: [c for c in self.b.walk(menu) if "menu-stickers-item" in c.ClassName.split()],
            lambda found: more_reaction_control(found, self.b) is not None,
        )
        more = more_reaction_control(items, self.b)
        if more is None:
            raise ControlUnavailable("Expected six quick reactions and one More entry")
        observed = set()
        for item in items:
            if self.b.auto.ControlsAreSame(item, more):
                continue
            if not item.IsEnabled:
                raise ControlUnavailable("Quick reaction is disabled")
            # The tooltip is the actual QQ label. This narrow hit-test exception
            # already covers the observed popup geometry; no coordinate click.
            with hover_control(self.b, item, allow_clipped_reaction=True):
                time.sleep(0.65)

                def tooltip():
                    tips = [
                        c
                        for c in self.b.walk(self.b.window)
                        if "sticker-list-tooltips-reaction" in c.ClassName.split()
                        and not c.IsOffscreen
                    ]
                    if len(tips) != 1:
                        return None
                    value = text_of(self.b.snapshot(tips[0])).strip()
                    return value or None

                try:
                    current = wait_until(tooltip, timeout=2)
                except ControlUnavailable as error:
                    # QQ can insert a quick item without an accessible label.
                    # Skip only an ordinary observation failure, never a more
                    # specific desktop/safety error or a submitted mutation.
                    if type(error) is not ControlUnavailable:
                        raise
                    continue
                if current not in QUICK_LABELS:
                    continue
                if current in observed:
                    raise ControlUnavailable(
                        "Quick reaction tooltip is ambiguous or stale"
                    )
                observed.add(current)
                if current == label:
                    pattern = item.GetPattern(self.b.auto.PatternId.InvokePattern)
                    if pattern is None:
                        raise ControlUnavailable(
                            "Quick reaction lacks native InvokePattern"
                        )
                    submitted[0] = True
                    pattern.Invoke(waitTime=0.1)
                    return
        raise ControlUnavailable("Requested quick reaction is not currently exposed")

    def _palette_submit(self, menu, label, submitted):
        items = wait_until(
            lambda: [c for c in self.b.walk(menu) if "menu-stickers-item" in c.ClassName.split()],
            lambda found: more_reaction_control(found, self.b) is not None,
        )
        more = more_reaction_control(items, self.b)
        if more is None:
            raise ControlUnavailable("Reaction More control is not unique")
        self.b.invoke(more)

        def choices():
            panel = self.ui.by_class("menu-stickers-panel")
            if panel is None:
                return []
            # Recently used emoji also appear in the canonical section. Scope
            # by QQ's actual heading, never choose an arbitrary duplicate.
            section, matches = None, []
            for c in panel.GetChildren():
                if "title" in c.ClassName.split():
                    section = text_of(self.b.snapshot(c))
                elif (section == "emoji 表情"
                      and "stickers-list-item" in c.ClassName.split()
                      and text_of(self.b.snapshot(c)) == label):
                    matches.append(c)
            return matches

        matches = wait_until(choices)
        if len(matches) != 1:
            raise ControlUnavailable("Reaction palette text is ambiguous")
        choice = matches[0]
        if choice.IsOffscreen:
            scroll = choice.GetPattern(self.b.auto.PatternId.ScrollItemPattern)
            if scroll is None:
                raise ControlUnavailable("Reaction item cannot be scrolled into view")
            scroll.ScrollIntoView(waitTime=0.1)
            # Chromium can rebuild entries while scrolling. Match the live
            # label again; never submit through an old index or UI object.
            matches = wait_until(choices)
            if len(matches) != 1:
                raise ControlUnavailable("Reaction palette text became ambiguous")
            choice = matches[0]
        if choice.IsOffscreen or not choice.IsEnabled:
            raise ControlUnavailable("Reaction palette item is not available")
        pattern = choice.GetPattern(self.b.auto.PatternId.InvokePattern)
        if pattern is None:
            raise ControlUnavailable("Reaction palette item lacks InvokePattern")
        submitted[0] = True
        pattern.Invoke(waitTime=0.1)

    def add_reaction(self, key, message_id, label, *, expected_text=None):
        if not isinstance(label, str) or not label.strip():
            raise ValueError("label must be nonempty native reaction text")
        if expected_text is not None and not isinstance(expected_text, str):
            raise TypeError("expected_text must be a string or None")
        submitted = [False]
        with mutation_guard(submitted), self.ui.checked(key) as target:
            self._validate(target, message_id, expected_text, writing=True)
            before = self._states(message_id)
            if any(c.own_selected for c in before):
                raise ControlUnavailable(
                    "Remove the existing own reaction first; QQ does not expose chip labels"
                )
            menu = self.ui.message_menu(target, message_id)
            palette = label not in QUICK_LABELS
            try:
                if palette:
                    self._palette_submit(menu, label, submitted)
                else:
                    self._quick_submit(menu, label, submitted)
            finally:
                self.ui._dismiss_menu()
                if palette and self.ui.by_class("menu-stickers-panel") is not None:
                    if editor_has_content(self.b.snapshot(self.b.editor())):
                        raise DraftNotEmpty("Draft appeared; reaction panel retained")
                    self.b.profile(target.id, target.name, target.kind)
                    wait_until(
                        lambda: self.ui.by_class("menu-stickers-panel"),
                        lambda panel: panel is None,
                    )

            def selected():
                self._validate(target, message_id, expected_text)
                own = [c for c in self._states(message_id) if c.own_selected]
                return own[0] if len(own) == 1 else None

            after = wait_until(selected, timeout=5)
            return ReactionReceipt(
                target.id,
                str(message_id),
                label,
                False,
                True,
                0 if not before else None,
                after.count,
            )

    def remove_own_reaction(self, key, message_id, *, expected_text=None):
        if expected_text is not None and not isinstance(expected_text, str):
            raise TypeError("expected_text must be a string or None")
        submitted = [False]
        with mutation_guard(submitted), self.ui.checked(key) as target:
            self._validate(target, message_id, expected_text, writing=True)
            chips = self._chips(message_id)
            own = [
                (c, state)
                for c in chips
                if (state := parse_reaction_chip(self.b.snapshot(c))).own_selected
            ]
            if len(own) > 1:
                raise ControlUnavailable(
                    "Multiple own reactions are ambiguous; none removed"
                )
            if not own:
                return ReactionReceipt(
                    target.id,
                    str(message_id),
                    None,
                    False,
                    False,
                    None,
                    None,
                    action="remove_own",
                )
            chip, before = own[0]
            if not chip.IsEnabled:
                raise ControlUnavailable("Own reaction chip is disabled")
            pattern = chip.GetPattern(self.b.auto.PatternId.InvokePattern)
            if pattern is None:
                raise ControlUnavailable("Own reaction chip lacks native InvokePattern")
            submitted[0] = True
            pattern.Invoke(waitTime=0.1)
            remaining = [None]

            def removed():
                self._validate(target, message_id, expected_text)
                states = self._states(message_id)
                remaining[0] = states
                return not any(c.own_selected for c in states)

            wait_until(removed, timeout=5)
            return ReactionReceipt(
                target.id,
                str(message_id),
                None,
                True,
                False,
                before.count,
                0 if not remaining[0] else None,
                action="remove_own",
            )
