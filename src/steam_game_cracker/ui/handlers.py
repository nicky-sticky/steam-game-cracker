import logging
from collections.abc import Callable

LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "dim",
    logging.INFO: "white",
    logging.WARNING: "yellow",
    logging.ERROR: "red",
    logging.CRITICAL: "bold red",
}


class TuiLogHandler(logging.Handler):
    """Route root logging records to a Textual RichLog callable."""

    def __init__(self, write_callable: Callable[[str], None]) -> None:
        super().__init__()
        self.write_callable = write_callable

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if "[" not in msg:
                color = LEVEL_COLORS.get(record.levelno, "white")
                self.write_callable(f"[{color}]{msg}[/{color}]")
            else:
                self.write_callable(msg)
        except Exception:
            self.handleError(record)
