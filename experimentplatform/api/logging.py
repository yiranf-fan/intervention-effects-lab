import json
import logging
import sys


def get_logger() -> logging.Logger:
    logger = logging.getLogger("experiment_api")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt='%(message)s'
    )
    handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def log_json(logger: logging.Logger, level: str, message: str, **kwargs):
    payload = {"msg": message, **kwargs}
    line = json.dumps(payload)
    getattr(logger, level)(line)
