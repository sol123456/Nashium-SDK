"""Allow ``python -m nashium.worker``.

``nashium farm`` launches workers this way instead of via the ``nashium-worker``
console script, so it works even when the Python Scripts directory is not on
PATH (the usual Windows annoyance) and always uses the same interpreter as the
supervisor.
"""

from __future__ import annotations

from .main import main

if __name__ == "__main__":
    main()
