"""
Spring extractor: builds the application model and reports the endpoints
impacted by a commit.
"""
import logging
import os
import re
import subprocess
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from tree_sitter import Language, Parser
from tree_sitter_java import language as java_language

from docai.core.files import iter_source_files
from docai.core.treesitter import reachable

from .analyzer import SpringAnalyzer, dedupe
from .architecture import ArchitectureBuilder
from .build_files import find_module_dirs, parse_modules
from .config_files import load_module_configs
from .endpoints import EndpointExtractor
from .java_index import JavaFileParser, JavaIndex, TypeRef, is_test_source, module_for
from .mybatis import load_mapper_xml

logger = logging.getLogger(__name__)


class SpringExtractor:

    def __init__(self):
        self.parser = Parser()
        self.parser.language = Language(java_language())
        self._reset(commit="HEAD")

    def _reset(self, commit):
        self._commit = commit
        self._repo_path = None
        self.index: Optional[JavaIndex] = None
        self.analyzer: Optional[SpringAnalyzer] = None
        self.endpoints: List[dict] = []
        self.architecture: Optional[dict] = None

    # ============================
    # MODEL
    # ============================

    def build_model(self, repo_path):
        """Parse sources, build/config files and analyze the whole application."""
        if self._repo_path == repo_path and self.analyzer is not None:
            return

        self._repo_path = repo_path
        module_dirs = find_module_dirs(repo_path)
        modules = parse_modules(repo_path, module_dirs)
        configs = load_module_configs(repo_path, module_dirs, lambda rel: module_for(rel, module_dirs))

        index = JavaIndex()
        for full in iter_source_files(repo_path, ".java"):
            rel = os.path.relpath(full, repo_path).replace(os.sep, "/")
            if is_test_source(rel):
                continue
            try:
                with open(full, "rb") as f:
                    code = f.read()
                tree = self.parser.parse(code)
                index.add_file(rel, JavaFileParser(code, rel, module_for(rel, module_dirs)).parse(tree.root_node))
            except Exception as e:
                logger.debug("Failed to index %s: %s", rel, e)
        index.finalize()

        analyzer = SpringAnalyzer(index, modules, configs, load_mapper_xml(repo_path))
        analyzer.analyze()

        self.index = index
        self.analyzer = analyzer
        self.endpoints = EndpointExtractor(analyzer).extract_all()
        self.architecture = ArchitectureBuilder(analyzer, self.endpoints).build()

        logger.info(
            "Spring model: %d types, %d endpoints, %d modules",
            len(index.types), len(self.endpoints), len(modules)
        )

    def all_routes(self, repo_path):
        """Every endpoint of the repository (full documentation, no diff)."""
        self._reset(commit="HEAD")
        self.build_model(repo_path)
        return [dict(e) for e in self.endpoints]

    def describe_application(self, repo_path=None):
        if repo_path is not None:
            self.build_model(repo_path)
        return self.architecture

    # ============================
    # MAIN PROCESS
    # ============================

    def process_repository(self, repo_path, changed_files, commit="HEAD"):
        self._reset(commit)
        self.build_model(repo_path)

        java_files = [f.replace(os.sep, "/") for f in changed_files if f.endswith(".java")]
        changed_methods, changed_types = self._changed_symbols(repo_path, java_files, commit)

        logger.debug("Changed methods: %s", sorted(changed_methods))
        logger.debug("Changed types: %s", sorted(changed_types))

        impacted = self._impacted_endpoints(changed_methods, changed_types)
        breaking = self._breaking_changes(repo_path, java_files, changed_types, commit)

        routes = []
        for endpoint in self.endpoints:
            key = (endpoint["controller"], endpoint["function"], endpoint["line"])
            if key not in impacted:
                continue

            route = dict(endpoint)
            route["change_reasons"] = sorted(impacted[key])

            changes = []
            for type_name in endpoint.get("referenced_types", []):
                changes.extend(breaking.get(type_name, []))
            if changes:
                route["breaking_changes"] = dedupe(changes)

            routes.append(route)

        logger.info("Impacted endpoints: %d of %d", len(routes), len(self.endpoints))
        return routes

    # ============================
    # CHANGE DETECTION
    # ============================

    def _changed_lines(self, repo_path, files, commit) -> Optional[Dict[str, List[Tuple[int, int]]]]:
        """{file: [(start, end)]} of lines changed by commit; None if the parent is unavailable."""
        if not files:
            return {}

        try:
            diff = subprocess.check_output(
                ["git", "diff", "-U0", f"{commit}^", commit, "--", *files],
                cwd=repo_path,
                stderr=subprocess.DEVNULL,
            ).decode(errors="replace")
        except (subprocess.CalledProcessError, OSError):
            return None

        changed: Dict[str, List[Tuple[int, int]]] = {}
        current = None

        for line in diff.splitlines():
            if line.startswith("+++ "):
                path = line[4:].strip()
                current = path[2:] if path.startswith("b/") else None
            elif line.startswith("@@") and current:
                match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
                if match:
                    start = int(match.group(1))
                    count = int(match.group(2)) if match.group(2) is not None else 1
                    changed.setdefault(current, []).append((start, start + max(count, 1) - 1))

        return changed

    def _changed_symbols(self, repo_path, java_files, commit) -> Tuple[Set[str], Set[str]]:
        changed_lines = self._changed_lines(repo_path, java_files, commit)
        methods: Set[str] = set()
        types: Set[str] = set()

        for rel in java_files:
            file_types = self.index.files.get(rel, [])
            hunks = [(1, 10 ** 9)] if changed_lines is None else changed_lines.get(rel, [])

            for start, end in hunks:
                for t in file_types:
                    if end < t.line_start or start > t.line_end:
                        continue

                    hit_methods = [
                        m for m in t.methods + t.constructors
                        if not (end < m.line_start or start > m.line_end)
                    ]
                    methods.update(m.id for m in hit_methods)

                    in_nested_type = any(
                        not (end < n.line_start or start > n.line_end)
                        for n in file_types if n.outer == t.qualified_name
                    )
                    if changed_lines is None or (not hit_methods and not in_nested_type):
                        # class-level change (annotations, fields): the whole type changed
                        types.add(t.qualified_name)
                        methods.update(m.id for m in t.methods + t.constructors)

        return methods, types

    def _impacted_endpoints(self, changed_methods: Set[str], changed_types: Set[str]) -> Dict[tuple, Set[str]]:
        a = self.analyzer
        reasons: Dict[str, Set[str]] = {m: {f"changed: {a.label(m)}"} for m in changed_methods}

        # methods that use a changed entity / repository through Spring Data
        changed_repos = {
            r["name"] for qn, r in a.repositories.items()
            if qn in changed_types or r.get("entity_qualified_name") in changed_types
        }
        if changed_repos:
            for mid, facts in a.facts.items():
                for f in facts:
                    if f.get("repository") in changed_repos:
                        reasons.setdefault(mid, set()).add(f"uses changed repository/entity: {f['repository']}")

        reverse: Dict[str, List[str]] = {}
        for caller, callees in a.calls.items():
            for callee in callees:
                reverse.setdefault(callee, []).append(caller)

        # propagate reasons to every (transitive) caller
        reached: Dict[str, Set[str]] = {}
        for start, why in reasons.items():
            queue, seen = deque([start]), {start}
            while queue:
                current = queue.popleft()
                reached.setdefault(current, set()).update(why)
                for caller in reverse.get(current, []):
                    if caller not in seen:
                        seen.add(caller)
                        queue.append(caller)

        impacted: Dict[tuple, Set[str]] = {}
        for e in self.endpoints:
            key = (e["controller"], e["function"], e["line"])
            handler_ids = [
                m.id for m in self.index.types[e["controller"]].methods
                if m.name == e["function"] and m.line_start == e["line"]
            ]
            for hid in handler_ids:
                if hid in reached:
                    impacted.setdefault(key, set()).update(reached[hid])

            for qn in changed_types & set(e.get("referenced_types", [])):
                impacted.setdefault(key, set()).add(f"changed model: {qn.rsplit('.', 1)[-1]}")

        return impacted

    # ============================
    # BREAKING CHANGES
    # ============================

    def get_old_file_content(self, repo_path, rel_path, commit=None):
        try:
            return subprocess.check_output(
                ["git", "show", f"{commit or self._commit}^:{rel_path}"],
                cwd=repo_path,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, OSError):
            return None

    def _field_contract(self, t, extractor: EndpointExtractor):
        return {
            f.name: {"type": str(f.type), "validation": extractor._validation(f.annotations, None, t)}
            for f in t.fields if not f.is_static
        }

    def _breaking_changes(self, repo_path, java_files, changed_types, commit) -> Dict[str, List[dict]]:
        """Contract changes of changed model types: {qualified type name: [change]}."""
        extractor = EndpointExtractor(self.analyzer)
        out: Dict[str, List[dict]] = {}

        for rel in java_files:
            new_types = {t.qualified_name: t for t in self.index.files.get(rel, []) if t.qualified_name in changed_types}
            if not new_types:
                continue

            old_code = self.get_old_file_content(repo_path, rel, commit)
            if not old_code:
                continue

            old_types = {
                t.qualified_name: t
                for t in JavaFileParser(old_code, rel, "").parse(self.parser.parse(old_code).root_node)
            }

            for qn, new_type in new_types.items():
                old_type = old_types.get(qn)
                if old_type is None:
                    continue
                changes = self.detect_breaking_changes(
                    self._field_contract(old_type, extractor),
                    self._field_contract(new_type, extractor),
                )
                for c in changes:
                    c["dto"] = new_type.name
                if changes:
                    out[qn] = changes

        return out

    def detect_breaking_changes(self, old_schema, new_schema):
        breaking = []

        for field, old_details in old_schema.items():
            if field not in new_schema:
                breaking.append({"type": "FIELD_REMOVED", "field": field, "old_type": old_details.get("type")})

        for field, new_details in new_schema.items():
            old_details = old_schema.get(field)
            new_validations = new_details.get("validation") or {}

            if old_details is None:
                breaking.append({
                    "type": "FIELD_ADDED", "field": field, "new_type": new_details.get("type"),
                    "required": bool(new_validations.get("required")),
                })
                continue

            if old_details.get("type") != new_details.get("type"):
                breaking.append({"type": "TYPE_CHANGED", "field": field,
                                 "old_type": old_details.get("type"), "new_type": new_details.get("type")})

            old_validations = old_details.get("validation") or {}

            for rule in old_validations:
                if rule not in new_validations:
                    breaking.append({"type": "VALIDATION_REMOVED", "field": field, "rule": rule})

            for rule in new_validations:
                if rule not in old_validations:
                    breaking.append({"type": "VALIDATION_ADDED", "field": field, "rule": rule})
                elif old_validations[rule] != new_validations[rule]:
                    breaking.append({"type": "VALIDATION_CHANGED", "field": field, "rule": rule,
                                     "old": old_validations[rule], "new": new_validations[rule]})

        return breaking

    # ============================
    # CONVENIENCE
    # ============================

    def extract_dto_schema(self, repo_path, dto_name):
        """Flat field schema of a DTO class (built from the full model)."""
        self.build_model(repo_path)
        target = self.index.resolve(dto_name)
        if target is None:
            return {}
        return EndpointExtractor(self.analyzer).schema_fields(TypeRef(target.name, raw=target.name), target, set())

    def expand_impact(self, func, graph):
        return reachable(func, graph, include_start_on_cycle=True)
