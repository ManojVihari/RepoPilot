from setuptools import setup, find_packages

setup(
    name="docai-scanner",
    version="0.1.0",
    description="DocAI API scanner for CI pipelines",
    author="DocAI",
    packages=find_packages(),
    install_requires=[
        "requests>=2.31.0",
        # Query/QueryCursor API used by the extractors needs tree-sitter 0.25+
        "tree-sitter>=0.25.0",
        "tree-sitter-java>=0.23.0",
        "tree-sitter-python>=0.23.0"
    ],
    entry_points={
        "console_scripts": [
            "docai-scan=docai.cli:main"
        ]
    },
    python_requires=">=3.8",
)
