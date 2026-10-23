import os
import sys
from collections import deque
from pathlib import Path
from typing import List
from loguru import logger

# Memóriabeli log puffer a gyors UI megjelenítéshez (utolsó 500 bejegyzés)
_LOG_BUFFER: deque = deque(maxlen=500)
_INITIALIZED = False


class _MemorySink:
    def __init__(self, buffer: deque):
        self.buffer = buffer

    def write(self, message):
        self.buffer.append(str(message).strip())


def setup_logging(project_root: Path = None):
    """
    Beállítja a naplózást:
    1. Fájlba írás: logs/app.log (5 MB-os rotációval, UTF-8 kódolással)
    2. Memóriapuffer a webes felület Hibakereső / Debug lapjához
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent.parent

    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    # Fájl sink hozzáadása
    try:
        logger.add(
            str(log_file),
            rotation="10 MB",
            retention="7 days",
            compression="zip",
            level="DEBUG",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
            enqueue=True,
        )
    except Exception as e:
        sys.stderr.write(f"Nem sikerült a log fájl hozzáadása: {e}\n")

    # Memória sink a webes UI-hoz
    try:
        mem_sink = _MemorySink(_LOG_BUFFER)
        logger.add(
            mem_sink.write,
            level="DEBUG",
            format="{time:HH:mm:ss} | {level:<7} | {message}",
            enqueue=False,
        )
    except Exception as e:
        sys.stderr.write(f"Nem sikerült a memória log sink hozzáadása: {e}\n")

    _INITIALIZED = True
    logger.info("Naplózó rendszer inicializálva (Fájl + Memória).")


def get_recent_logs(limit: int = 150) -> List[str]:
    """Visszaadja a legfrissebb log bejegyzéseket időrendi sorrendben."""
    items = list(_LOG_BUFFER)
    return items[-limit:] if len(items) > limit else items


def get_log_text(limit: int = 150) -> str:
    """Visszaadja a legfrissebb logokat egyetlen szövegként."""
    return "\n".join(get_recent_logs(limit))


def clear_memory_logs():
    """Törli a memóriapuffert."""
    _LOG_BUFFER.clear()
