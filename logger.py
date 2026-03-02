# logger.py
from loguru import logger
import sys


class BotLogger:
    """Centralized logging for the MBFC scraper bot."""

    def __init__(self, log_level: str = "INFO"):
        logger.remove()

        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level=log_level,
            colorize=True
        )

        logger.add(
            "logs/scraper_{time:YYYY-MM-DD}.log",
            rotation="200 MB",
            retention="14 days",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}"
        )

        logger.add(
            "logs/errors_{time:YYYY-MM-DD}.log",
            rotation="50 MB",
            retention="30 days",
            level="ERROR",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}"
        )

        self.logger = logger


bot_logger = BotLogger()
