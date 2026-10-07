"""Where log records go: the one dictConfig, in _default_config(). Called only by a
front end. Not in audit_config.json, which may be broken or depend on the cwd."""

import json
import logging
import logging.config
import os
import sys
import time

LEVEL_ENV = "AUDIT_LOG_LEVEL"
CONFIG_ENV = "AUDIT_LOG_CONFIG"
CONFIG_FILENAME = "logging.json"

CORE_LOGGER = "aws_resource_audit"
CONSOLE_LOGGER = "aws_resource_audit.console"
BACKEND_LOGGER = "app"

LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")

# Pinned at WARNING: DEBUG here would log every signed AWS request and every LLM prompt.
_THIRD_PARTY = (
    "boto3", "botocore", "urllib3", "s3transfer",
    "httpx", "httpcore", "openai", "anthropic", "chromadb",
    "langchain", "langchain_core", "langgraph", "sentence_transformers",
    "asyncio", "watchfiles", "multipart",
)


class UtcFormatter(logging.Formatter):
    """UTC timestamps. logging's default is local time with no offset, which is
    unreadable beside a snapshot whose every date is UTC."""

    converter = time.gmtime


def _default_config(level):
    """The built-in configuration. This dict IS the destination decision."""
    return {
        "version": 1,
        # False is load-bearing. uvicorn installs its own dictConfig before it
        # imports app.main, and True would switch off uvicorn.error/access.
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {
                "()": "aws_resource_audit.logsetup.UtcFormatter",
                "format": "%(asctime)sZ %(levelname)-7s %(name)s %(message)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "plain",
                # stderr: stdout carries the scan's progress lines, which are
                # written with end="" and filled in in place.
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            CORE_LOGGER: {"level": level, "handlers": ["console"],
                          "propagate": False},
            BACKEND_LOGGER: {"level": level, "handlers": ["console"],
                             "propagate": False},
            # No handler, deliberately. console.py mirrors say/detail/warn here
            # and has already printed them; a handler would print each twice.
            CONSOLE_LOGGER: {"level": level, "handlers": [], "propagate": False},
            **{name: {"level": "WARNING"} for name in _THIRD_PARTY},
        },
        # WARNING, not `level`: raising the root is how DEBUG reaches every
        # library above.
        "root": {"level": "WARNING", "handlers": ["console"]},
    }


def resolve_level(debug=False):
    """AUDIT_LOG_LEVEL, else --debug, else INFO. A bad value warns and falls back,
    so a typo cannot stop a scan."""
    raw = os.environ.get(LEVEL_ENV)
    if raw:
        name = raw.strip().upper()
        if name in LEVELS:
            return name
        print(f"{LEVEL_ENV}={raw!r} is not a log level "
              f"({', '.join(LEVELS)}); using INFO", file=sys.stderr)
    return "DEBUG" if debug else "INFO"


def _file_config(path):
    """A logging.json, or None. Never searched for in the cwd: dictConfig can
    instantiate arbitrary callables, so that would be code execution."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as e:
        print(f"{path}: could not be read ({e}); using the built-in logging "
              "configuration", file=sys.stderr)
        return None


_configured = False


def configure(debug=False, config_path=None, force=False):
    """Install logging. Called once, by a front end - never by the library.
    config_path replaces the built-in default; AUDIT_LOG_CONFIG overrides it."""
    global _configured
    if _configured and not force:
        return
    level = resolve_level(debug)
    config = _file_config(os.environ.get(CONFIG_ENV) or config_path)
    if config is None:
        config = _default_config(level)
    else:
        config.setdefault("disable_existing_loggers", False)
    try:
        logging.config.dictConfig(config)
    except (ValueError, TypeError, AttributeError, ImportError) as e:
        print(f"logging configuration failed ({e}); using the built-in one",
              file=sys.stderr)
        logging.config.dictConfig(_default_config(level))
    _configured = True
