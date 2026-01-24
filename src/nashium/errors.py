class NashiumError(Exception):
    pass


class BotLoadError(NashiumError):
    pass


class BotTimeoutError(NashiumError):
    pass


class InvalidMoveError(NashiumError):
    pass
