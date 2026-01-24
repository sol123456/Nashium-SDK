class NashiumError(Exception):
    pass


class BotLoadError(NashiumError):
    pass


class BotTimeoutError(NashiumError):
    pass


class InvalidMoveError(NashiumError):
    pass


class BotRuntimeError(NashiumError):
    """Wraps errors that occur during bot execution with location info."""
    def __init__(self, message: str, original_error: Exception,
                 filename: str = None, lineno: int = None, line_text: str = None):
        super().__init__(message)
        self.original_error = original_error
        self.filename = filename
        self.lineno = lineno
        self.line_text = line_text