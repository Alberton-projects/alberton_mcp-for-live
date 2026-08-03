"""Colors: '#RRGGBB' at the tool boundary, integer RGB on the wire."""

from .errors import ToolError


def to_int(color):
    if isinstance(color, int):
        value = color
    elif isinstance(color, str):
        text = color.strip().lstrip("#")
        if len(text) != 6:
            raise ToolError("invalid_argument",
                            "color must be '#RRGGBB', got %r" % color)
        try:
            value = int(text, 16)
        except ValueError:
            raise ToolError("invalid_argument",
                            "color must be '#RRGGBB', got %r" % color)
    else:
        raise ToolError("invalid_argument",
                        "color must be '#RRGGBB', got %r" % (color,))
    if not 0 <= value <= 0xFFFFFF:
        raise ToolError("invalid_argument", "color out of RGB range: %r" % (color,))
    return value


def to_hex(value):
    if not isinstance(value, int):
        return value
    return "#%06X" % (value & 0xFFFFFF)
