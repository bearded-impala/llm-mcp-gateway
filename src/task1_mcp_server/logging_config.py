"""Logging configuration for MCP stdio transport.

Redirects application and root logs to stderr so stdout remains clean
for JSON-RPC transport framing.
"""

import logging
import sys


def setup_mcp_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("mcp_server")
    logger.setLevel(level)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [mcp_server] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)
    logger.propagate = False

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.addHandler(stderr_handler)

    return logger
