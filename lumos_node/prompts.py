from functools import lru_cache
from pathlib import Path

from .config import get_settings


class SystemPromptError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_system_prompt(path: Path | None = None) -> str:
    settings = get_settings()
    target = path if path is not None else settings.system_prompt_path
    target = target.expanduser()
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    if not target.exists():
        raise SystemPromptError(
            f"System prompt not found at {target}. "
            f"Set LUMOS_SYSTEM_PROMPT_PATH or place the cheat sheet at the configured path."
        )
    text = target.read_text(encoding="utf-8")
    if not text.strip():
        raise SystemPromptError(f"System prompt at {target} is empty.")
    return text


def reload_system_prompt() -> str:
    load_system_prompt.cache_clear()
    return load_system_prompt()
