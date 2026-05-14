import logging
from logging.handlers import RotatingFileHandler

from config import LOG_DIR

_configured = False


def get_logger(name: str = "binance_eth") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if _configured:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger("binance_eth")
    root.setLevel(logging.INFO)
    root.addHandler(stream)
    root.addHandler(file_handler)
    root.propagate = False

    _configured = True
    return logger
