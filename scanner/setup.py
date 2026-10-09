from setuptools import setup, find_packages

setup(
    name="mergeclear",
    version="0.2.0",
    description="Know what a change breaks before you merge it: API contract and dependency scanner",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    license="Apache-2.0",
    packages=find_packages(exclude=["tests", "tests.*"]),
    install_requires=[
        "requests>=2.31.0",
        # Query/QueryCursor API used by the extractors needs tree-sitter 0.25+
        "tree-sitter>=0.25.0",
        "tree-sitter-java>=0.23.0",
        "tree-sitter-python>=0.23.0",
        "pyyaml>=6.0",
    ],
    entry_points={"console_scripts": ["mergeclear=mergeclear.cli:main"]},
    python_requires=">=3.10",
)
