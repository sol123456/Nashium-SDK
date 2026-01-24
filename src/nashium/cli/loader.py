from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

from ..core.errors import BotLoadError


def load_bot_from_file(path: str | Path, seed: int):
    """
    Dynamically load a bot from a Python file.

    WARNING: This executes arbitrary code! Only use for LOCAL testing.
    The server uses Docker containers for isolation.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise BotLoadError(f"Bot file not found: {p}")

    module_name = f"nashium_userbot_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, str(p))
    if spec is None or spec.loader is None:
        raise BotLoadError(f"Failed to import bot file: {p}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
    except SyntaxError as e:
        raise e  # Re-raise SyntaxError directly for better handling
    except Exception as e:
        raise BotLoadError(f"Error executing bot file: {e}") from e

    create_bot = getattr(module, "create_bot", None)
    if create_bot is None or not callable(create_bot):
        raise BotLoadError(
            "Bot file must define a callable create_bot(seed: int) that returns an object with move(state) -> 0|1"
        )

    try:
        bot = create_bot(int(seed))
    except Exception as e:
        raise BotLoadError(f"Error in create_bot(): {e}") from e

    if bot is None or not hasattr(bot, "move"):
        raise BotLoadError("create_bot(...) must return an object with a move(state) method")

    return bot