import logging
from mergeclear.core.files import iter_source_files
from mergeclear.plugins.base_plugin import BasePlugin
from .extractor import SpringExtractor

logger = logging.getLogger(__name__)

SPRING_MARKERS = (
    "@RestController",
    "@Controller",
    "@RequestMapping",
    "@GetMapping",
    "@PostMapping"
)


class Plugin(BasePlugin):

    name = "spring"

    def __init__(self):
        self.extractor = SpringExtractor()

    def detect(self, repo_path):
        logger.debug("Running Spring detection...")

        for full in iter_source_files(repo_path, ".java"):
            try:
                with open(full, "r", encoding="utf-8") as file:
                    content = file.read()
            except (OSError, UnicodeDecodeError):
                continue

            if any(x in content for x in SPRING_MARKERS):
                logger.debug("Detected Spring in: %s", full)
                return True

        return False
