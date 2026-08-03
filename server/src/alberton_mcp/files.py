"""Audio file validation for clip import.

Policy (decided with the user, 2026-08-03): any absolute path is allowed —
the server already runs with the user's own permissions and Live can open any
file — but it is validated here so failures arrive as structured errors with a
usable hint instead of an opaque LOM exception from inside Live.
"""

import os

from .errors import ToolError

# Formats Live reads. Override with ALBERTON_AUDIO_EXTENSIONS (comma separated)
# if a build supports more.
DEFAULT_EXTENSIONS = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a",
                      ".aac", ".mp4", ".wv", ".caf")


def audio_extensions():
    override = os.environ.get("ALBERTON_AUDIO_EXTENSIONS")
    if not override:
        return DEFAULT_EXTENSIONS
    return tuple(e if e.startswith(".") else "." + e
                 for e in (part.strip().lower() for part in override.split(","))
                 if e)


def validate_audio_path(path):
    """Return the normalized absolute path, or raise a structured ToolError."""
    if not isinstance(path, str) or not path.strip():
        raise ToolError("invalid_argument", "file_path must be a non-empty string")
    expanded = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isabs(expanded):
        raise ToolError("invalid_argument",
                        "file_path must be absolute, got %r" % path)
    if not os.path.exists(expanded):
        parent = os.path.dirname(expanded)
        hint = ("the folder %r does not exist either" % parent
                if not os.path.isdir(parent)
                else "the folder exists; check the file name")
        raise ToolError("not_found", "no file at %s" % expanded, hint=hint)
    if os.path.isdir(expanded):
        raise ToolError("invalid_argument",
                        "%s is a folder, not an audio file" % expanded)
    if not os.path.isfile(expanded):
        raise ToolError("invalid_argument",
                        "%s is not a regular file" % expanded)
    extensions = audio_extensions()
    if os.path.splitext(expanded)[1].lower() not in extensions:
        raise ToolError("invalid_argument",
                        "%s is not a recognised audio format" % expanded,
                        hint="expected one of: %s (override with "
                             "ALBERTON_AUDIO_EXTENSIONS)" % ", ".join(extensions))
    if not os.access(expanded, os.R_OK):
        raise ToolError("invalid_argument", "%s is not readable" % expanded)
    if os.path.getsize(expanded) == 0:
        raise ToolError("invalid_argument", "%s is empty" % expanded)
    return expanded
