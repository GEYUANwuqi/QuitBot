"""Transient AT-SPI read failures while the client rebuilds its accessible tree."""


def is_transient_error(error):
    from gi.repository import GLib

    if not isinstance(error, GLib.Error):
        return False
    message = str(error)
    return any(
        name in message
        for name in (
            "org.freedesktop.DBus.Error.NoReply",
            "org.freedesktop.DBus.Error.UnknownObject",
            "org.a11y.atspi.Error.InvalidObject",
            "timeout from dbind",
        )
    ) or ("could not be processed as object " in message and "does not exist" in message)
