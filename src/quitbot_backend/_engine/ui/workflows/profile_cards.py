"""Find QQ's cached profile windows without trusting stale hidden content."""

from quitbot_backend._engine.errors import ControlUnavailable, TargetMismatch


def native_cards(auto, pid):
    if hasattr(auto, "native_cards"):
        return auto.native_cards(pid)
    return []


class ProfileCards:
    """Capture before the opening action; hidden cards must change afterwards."""

    def __init__(self, backend):
        self.b = backend
        self.pid = backend.window.ProcessId
        self.before = {}
        for card, visible in self.candidates():
            if visible:
                raise ControlUnavailable("Close the existing profile card first")
            self.before[self.key(card)] = self.fields(card)

    @staticmethod
    def key(card):
        return getattr(card, "NativeWindowHandle", id(card))

    def fields(self, card):
        return tuple(
            x.Name
            for x in self.b.walk(card)
            if x.ControlTypeName == "TextControl" and x.Name
        )

    def candidates(self):
        native = native_cards(self.b.auto, self.pid)
        seen = {self.key(card) for card, _ in native}
        result = list(native)
        for card in self.b.auto.GetRootControl().GetChildren():
            if (
                card.ProcessId == self.pid
                and card.Name == "资料卡"
                and self.key(card) not in seen
            ):
                result.append((card, True))
        return result

    def refreshed(self, accept):
        matches = []
        for card, visible in self.candidates():
            fields = self.fields(card)
            if not visible and self.before.get(self.key(card)) == fields:
                continue
            if accept(fields):
                matches.append((card, visible, fields))
        if len(matches) > 1:
            raise TargetMismatch("Ambiguous refreshed QQ profile cards")
        return matches[0] if matches else None

    def reveal(self, result):
        card, visible, _ = result
        if hasattr(self.b.auto, "reveal_profile"):
            return self.b.auto.reveal_profile(card)
        if not visible:
            from .inspection import require_unlocked

            require_unlocked(self.b.auto)
            card.SetActive(waitTime=0.1)
        return card
