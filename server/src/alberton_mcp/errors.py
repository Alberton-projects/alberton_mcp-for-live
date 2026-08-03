"""Structured tool errors (CONTRACT Layer B conventions).

Tools never signal failure with prose: they return {"error": {code, message,
hint?}}. Inside the implementation we raise ToolError and the server layer
converts; wire-level failures (WireError / BridgeUnreachable) are mapped there
too, so every failure the model sees has the same shape.
"""


class ToolError(Exception):
    def __init__(self, code, message, hint=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self):
        error = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        return {"error": error}
