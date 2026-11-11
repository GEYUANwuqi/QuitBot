class QuitBotError(RuntimeError):
    pass


class ControlUnavailable(QuitBotError):
    pass


class TargetMismatch(QuitBotError):
    pass


class DraftNotEmpty(QuitBotError):
    pass


class SendOutcomeUnknown(QuitBotError):
    """Do not retry automatically: the message may already have been sent."""


class MutationOutcomeUnknown(QuitBotError):
    """A state-changing action was submitted; inspect before retrying."""


class FocusLost(QuitBotError):
    pass


class MessageNotFound(QuitBotError):
    pass


class InstanceClosed(QuitBotError):
    pass
