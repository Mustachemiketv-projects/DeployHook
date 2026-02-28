"""
In-memory log ring buffer + optional file sink.

Call setup() once at startup. All loggers (including uvicorn) will feed here.
"""
import logging
import os
import sys
from collections import deque
from logging.handlers import RotatingFileHandler

MAX_LINES = 1000

_buffer: deque = deque(maxlen=MAX_LINES)

_LOG_FILE = "/app/data/app.log"


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            _buffer.append(self.format(record))
        except Exception:
            pass


def get_lines() -> list[str]:
    return list(_buffer)


def setup():
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    buf_handler = _BufferHandler()
    buf_handler.setFormatter(fmt)

    # Optional file sink (rotates at 2 MB, keeps 2 backups)
    os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
    file_handler = RotatingFileHandler(_LOG_FILE, maxBytes=2_000_000, backupCount=2)
    file_handler.setFormatter(fmt)

    # stdout sink so `docker logs` captures output
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (buf_handler, file_handler, stdout_handler):
        root.addHandler(h)

    # Pull uvicorn's own loggers into the same handlers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True   # let root handle it
