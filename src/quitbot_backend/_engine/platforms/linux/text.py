"""Read AT-SPI editable text, expanding embedded paragraph objects in place."""

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from ...errors import ControlUnavailable


def text_value(obj, ancestors=()):
    identity = (obj.app.bus_name, obj.path)
    if identity in ancestors or len(ancestors) >= 35:
        raise ControlUnavailable("Cyclic or excessively deep AT-SPI text")
    obj.clear_cache()
    value = Atspi.Text.get_text(obj, 0, -1)
    if "\ufffc" not in value or "Hypertext" not in obj.get_interfaces():
        return value
    parts = []
    for offset, char in enumerate(value):
        if char != "\ufffc":
            parts.append(char)
            continue
        index = Atspi.Hypertext.get_link_index(obj, offset)
        if index < 0:
            parts.append(char)  # A literal object-replacement character typed by the user.
            continue
        link = Atspi.Hypertext.get_link(obj, index)
        child = Atspi.Hyperlink.get_object(link, 0) if link is not None else None
        if child is None or "Text" not in child.get_interfaces():
            raise ControlUnavailable("Embedded editable text cannot be read completely")
        parts.append(text_value(child, (*ancestors, identity)))
    return "".join(parts)
