class NashiumError(Exception):
    pass


class MatchExecutionError(NashiumError):
    """Harness/infrastructure failure (Docker down, image missing, container broken).

    NOT used for bot faults - those are recorded on RuntimeStats instead so the
    match still produces a submittable result.
    """


class BotLoadError(NashiumError):
    """Kept for compatibility; bot load failures are normally recorded, not raised."""