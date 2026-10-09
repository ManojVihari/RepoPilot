import logging
import pkgutil
import importlib

logger = logging.getLogger(__name__)


class PluginManager:

    def load_plugins(self):

        plugins = []
        package = "mergeclear.plugins"

        try:
            base_module = importlib.import_module(package)
        except ModuleNotFoundError:
            logger.error("Package not found: %s", package)
            return []

        logger.debug("Scanning plugins in: %s", list(base_module.__path__))

        for finder, name, ispkg in pkgutil.iter_modules(base_module.__path__):

            logger.debug("Found: %s, ispkg=%s", name, ispkg)

            try:
                # Try loading plugin module inside folder
                module = importlib.import_module(
                    f"{package}.{name}.plugin"
                )
            except ModuleNotFoundError as e:
                if e.name and e.name.startswith(f"{package}.{name}"):
                    logger.debug("Skipping %s: plugin module not found (%s)", name, e)
                else:
                    # the plugin exists but one of its libraries is not installed
                    logger.error(
                        "Plugin %s needs the Python package '%s', which is not installed. "
                        "Run: pip install -r scanner/requirements.txt",
                        name, e.name,
                    )
                continue
            except Exception as e:
                logger.error("Failed loading %s: %s", name, e)
                continue

            plugin_class = getattr(module, "Plugin", None)

            if not plugin_class:
                logger.warning("No Plugin class in %s", name)
                continue

            try:
                plugin = plugin_class()
                logger.info("Loaded plugin: %s", plugin.name)
                plugins.append(plugin)
            except Exception as e:
                logger.error("Failed to initialize plugin %s: %s", name, e)

        if not plugins:
            logger.warning("No plugins loaded")

        return plugins
