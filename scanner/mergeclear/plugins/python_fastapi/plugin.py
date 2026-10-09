import logging
from mergeclear.core.files import iter_source_files
from mergeclear.plugins.base_plugin import BasePlugin
from .extractor import FastAPIExtractor

logger = logging.getLogger(__name__)


class Plugin(BasePlugin):

    name = "fastapi"

    def __init__(self):
        self.extractor = FastAPIExtractor()

    def detect(self, repo_path):
        logger.debug("Running FastAPI detection...")

        for full in iter_source_files(repo_path, ".py"):
            try:
                with open(full, "r", encoding="utf-8") as file:
                    content = file.read().lower()
            except (OSError, UnicodeDecodeError) as e:
                logger.debug("Error reading %s: %s", full, e)
                continue

            if "fastapi" in content:
                logger.debug("Detected FastAPI in: %s", full)
                return True

        return False
