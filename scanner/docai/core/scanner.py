import subprocess
import os
from docai.core.plugin_manager import PluginManager


class Scanner:

    def __init__(self):
        self.plugin_manager = PluginManager()

    def scan(self, repo_path, commit):

        try:
            changed_files = self.get_changed_files(repo_path, commit)
        except Exception as e:
            print(f"[ERROR] Failed to get changed files: {e}")
            changed_files = []

        if not changed_files:
            print("[INFO] No changed files detected")
            return self._empty_result(repo_path, commit)

        plugins = self.plugin_manager.load_plugins()

        all_routes = []
        detected_frameworks = []

        for plugin in plugins:
            try:
                if plugin.detect(repo_path):
                    detected_frameworks.append(plugin.name)

                    routes = plugin.extractor.process_repository(
                        repo_path,
                        changed_files
                    )

                    all_routes.extend(routes)

            except Exception as e:
                print(f"[ERROR] Plugin {plugin.name} failed: {e}")

        if not detected_frameworks:
            print("[WARN] No frameworks detected")

        return {
            "scanner_version": "1.1",
            "repository": os.path.basename(repo_path),
            "commit": commit,
            "frameworks": detected_frameworks,
            "routes": all_routes
        }

    # def get_changed_files(self, repo_path, commit):

    #     try:
    #         result = subprocess.check_output(
    #             ["git", "diff", "--name-only", f"{commit}~1", commit],
    #             cwd=repo_path,
    #             stderr=subprocess.STDOUT
    #         )

    #         files = result.decode().splitlines()

    #         return [
    #             f for f in files
    #             if f.endswith((".py", ".java"))
    #         ]

    #     except subprocess.CalledProcessError as e:
    #         raise Exception(e.output.decode())
    def get_changed_files(self, repo_path, commit):
        try:
            result = subprocess.check_output(
                ["git", "ls-files"],
                cwd=repo_path,
                stderr=subprocess.STDOUT
            )
    
            files = result.decode().splitlines()
    
            print(f"Found {len(files)} git tracked files")
            print(files[:20])
    
            filtered = [
                f for f in files
                if f.endswith((".py", ".java"))
            ]
    
            print(f"Found {len(filtered)} source files")
    
            return filtered
    
        except subprocess.CalledProcessError as e:
            raise Exception(e.output.decode())

    def _empty_result(self, repo_path, commit):
        return {
            "scanner_version": "1.1",
            "repository": os.path.basename(repo_path),
            "commit": commit,
            "frameworks": [],
            "routes": []
        }
