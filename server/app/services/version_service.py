import os
import json
from app.config import DATABASE_DIR

class VersionService:

    def __init__(self, base_path=DATABASE_DIR):
        self.base_path = base_path
        os.makedirs(base_path, exist_ok=True)

    def _get_file(self, repository, api_name):

        repo_dir = os.path.join(self.base_path, repository)
        os.makedirs(repo_dir, exist_ok=True)

        return os.path.join(repo_dir, f"{api_name}.json")

    def get_versions(self, repository, api_name):

        file_path = self._get_file(repository, api_name)

        if not os.path.exists(file_path):
            return []

        with open(file_path, "r") as f:
            return json.load(f)

    def get_latest(self, repository, api_name):

        versions = self.get_versions(repository, api_name)

        if not versions:
            return None

        return versions[-1]

    def should_create_version(self, repository, api_name, signature):

        latest = self.get_latest(repository, api_name)

        if not latest:
            return True, 1

        if latest["signature"] == signature:
            return False, latest["version"]

        return True, latest["version"] + 1


    def save_version(self, repository, api_name, version, signature, commit_hash, content):

        file_path = self._get_file(repository, api_name)

        versions = self.get_versions(repository, api_name)

        new_entry = {
            "version": version,
            "signature": signature,
            "commit_hash": commit_hash,
            "content": content
        }

        versions.append(new_entry)

        with open(file_path, "w") as f:
            json.dump(versions, f, indent=2)

        print(f"[NEW VERSION] {api_name} v{version}")
