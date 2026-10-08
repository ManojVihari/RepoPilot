"""
MyBatis mapper XML files: <mapper namespace="com.x.FooMapper"> statements.
"""
import logging
import os
import re
import xml.etree.ElementTree as ET

from docai.core.files import SKIP_DIRS

logger = logging.getLogger(__name__)

MAX_XML_SIZE = 2_000_000
STATEMENT_OPERATIONS = {"select": "READ", "insert": "WRITE", "update": "WRITE", "delete": "DELETE"}


def load_mapper_xml(repo_path):
    """{namespace: {"file", "entity", "statements": {id: {"operation", "sql"}}}}"""
    mappers = {}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = os.path.relpath(root, repo_path).replace(os.sep, "/")
        if "/src/test" in f"/{rel_root}":
            continue

        for f in files:
            if not f.endswith(".xml") or f == "pom.xml":
                continue

            path = os.path.join(root, f)
            try:
                if os.path.getsize(path) > MAX_XML_SIZE:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                if "<mapper" not in text or "namespace" not in text:
                    continue
                mapper = _parse_mapper(text)
            except Exception as e:
                logger.debug("Failed to parse MyBatis mapper %s: %s", path, e)
                continue

            if mapper:
                namespace, info = mapper
                info["file"] = os.path.relpath(path, repo_path).replace(os.sep, "/")
                mappers[namespace] = info

    return mappers


def _parse_mapper(text):
    # DOCTYPE lines reference an external DTD; ElementTree never fetches it
    root = ET.fromstring(text.encode("utf-8"))
    if root.tag != "mapper" or not root.get("namespace"):
        return None

    fragments = {el.get("id"): _sql_text(el, {}) for el in root.findall("sql")}

    entity = None
    for result_map in root.findall("resultMap"):
        if result_map.get("id") == "BaseResultMap" or entity is None:
            entity = result_map.get("type") or entity

    statements = {}
    for el in root:
        operation = STATEMENT_OPERATIONS.get(el.tag)
        if operation and el.get("id"):
            statements[el.get("id")] = {
                "operation": operation,
                "sql": _sql_text(el, fragments),
            }

    return root.get("namespace"), {"entity": entity, "statements": statements}


def _sql_text(element, fragments):
    """Statement text with <include refid> fragments expanded; dynamic tags flattened."""
    parts = [element.text or ""]

    for child in element:
        if child.tag == "include":
            parts.append(fragments.get(child.get("refid"), ""))
        else:
            parts.append(_sql_text(child, fragments))
        parts.append(child.tail or "")

    return re.sub(r"\s+", " ", "".join(parts)).strip()
