class BasePlugin:
    """
    A framework plugin.

    The scanner calls `detect(repo_path)` and, when it returns True,
    `extractor.process_repository(repo_path, changed_files, commit)`.
    """

    name = "base"
    extractor = None

    def detect(self, repo_path):
        raise NotImplementedError
