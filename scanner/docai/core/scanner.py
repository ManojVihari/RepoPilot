import logging
import subprocess
import os
from docai.core.plugin_manager import PluginManager

logger = logging.getLogger(__name__)

SCANNER_VERSION = "2.0"
SOURCE_EXTENSIONS = (".py", ".java")


class Scanner:

    def __init__(self):
        self.plugin_manager = PluginManager()

    def scan(self, repo_path, commit):

        try:
            changed_files = self.get_changed_files(repo_path, commit)
        except Exception as e:
            logger.error("Failed to get changed files: %s", e)
            changed_files = []

        if not changed_files:
            logger.info("No changed files detected")
            return self._empty_result(repo_path, commit)

        plugins = self.plugin_manager.load_plugins()

        all_routes = []
        detected_frameworks = []
        architecture = {}

        for plugin in plugins:
            try:
                if plugin.detect(repo_path):
                    detected_frameworks.append(plugin.name)

                    routes = plugin.extractor.process_repository(
                        repo_path,
                        changed_files,
                        commit
                    )

                    all_routes.extend(routes)

                    describe = getattr(plugin.extractor, "describe_application", None)
                    if describe is not None:
                        architecture[plugin.name] = describe()

            except Exception as e:
                logger.exception("Plugin %s failed: %s", plugin.name, e)

        if not detected_frameworks:
            logger.warning("No frameworks detected")

        return {
            "scanner_version": SCANNER_VERSION,
            "repository": os.path.basename(repo_path),
            "commit": commit,
            "frameworks": detected_frameworks,
            "routes": all_routes,
            "architecture": architecture
        }

    def get_changed_files(self, repo_path, commit):
        """
        Source files changed by `commit` compared to its first parent.

        Falls back to every tracked source file when the parent is not
        available (first commit, or a shallow CI clone).
        """
        try:
            files = self._git(repo_path, "diff", "--name-only", f"{commit}^", commit)
        except subprocess.CalledProcessError:
            logger.info("No parent commit for %s; scanning all tracked files", commit)
            files = self._git(repo_path, "ls-files")

        filtered = [f for f in files if f.endswith(SOURCE_EXTENSIONS)]

        logger.info("Found %d changed source files", len(filtered))

        return filtered

    def _git(self, repo_path, *args):
        result = subprocess.check_output(
            ["git", *args],
            cwd=repo_path,
            stderr=subprocess.STDOUT
        )
        return result.decode().splitlines()

    def _empty_result(self, repo_path, commit):
        return {
            "scanner_version": SCANNER_VERSION,
            "repository": os.path.basename(repo_path),
            "commit": commit,
            "frameworks": [],
            "routes": [],
            "architecture": {}
        }
