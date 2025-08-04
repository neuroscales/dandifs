import logging
import os


def get_logger(name=None):
    """Return a logger to use"""
    return logging.getLogger("dandifs" + (".%s" % name if name else ""))


def set_logger_level(lgr, level):
    if isinstance(level, int):
        pass
    elif level.isnumeric():
        level = int(level)
    elif level.isalpha():
        level = getattr(logging, level)
    else:
        lgr.warning("Do not know how to treat loglevel %s" % level)
        return
    lgr.setLevel(level)


LOG = get_logger()
set_logger_level(LOG, os.environ.get("DANDI_LOG_LEVEL", logging.INFO))
