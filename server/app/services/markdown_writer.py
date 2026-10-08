import os
from app.config import DOCS_DIR


class MarkdownWriter:

    def __init__(self, base_path=DOCS_DIR):
        self.base_path = base_path
    
    def write(self, repository: str, api_name: str, version: int, content: str):

        """
        Writes documentation as:
        docs/<repo>/<api_name>/v<version>.md
        """

        repo_path = os.path.join(self.base_path, repository)
        api_path = os.path.join(repo_path, api_name)

        os.makedirs(api_path, exist_ok=True)

        file_path = os.path.join(api_path, f"v{version}.md")

        with open(file_path, "w") as f:
            f.write(content)

        print(f"[DOC WRITTEN] {file_path}")
