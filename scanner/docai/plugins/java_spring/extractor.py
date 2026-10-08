from tree_sitter import Parser, Language, Query, QueryCursor
from tree_sitter_java import language as java_language
from docai.core.files import iter_source_files
from docai.core.treesitter import normalize_captures, get_parent, get_text, reachable
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)


class SpringExtractor:

    def __init__(self):
        self.parser = Parser()
        self.language = Language(java_language())
        self.parser.language = self.language

        # API detection
        self.query = Query(self.language, """
        (
          (method_declaration
            (modifiers
              (annotation
                name: (identifier) @annotation
                arguments: (annotation_argument_list
                  (string_literal) @path
                )
              )
            )
            name: (identifier) @func_name
          )
        )
        """)

        self._reset_caches(commit="HEAD")

    def _reset_caches(self, commit):
        self._commit = commit
        self._parsed = {}
        self._java_files = None
        self._dto_files = None
        self._dto_schemas = {}

    def _parse(self, file_path):
        """(code, tree) for a file, parsed at most once per run."""

        key = os.path.normpath(file_path)

        if key not in self._parsed:
            with open(file_path, "rb") as f:
                code = f.read()

            self._parsed[key] = (code, self.parser.parse(code))

        return self._parsed[key]

    def _iter_java_files(self, repo_path):
        if self._java_files is None:
            self._java_files = list(iter_source_files(repo_path, ".java"))

        return self._java_files

    def _find_dto_files(self, repo_path, dto_name):
        """Files declaring `dto_name`, i.e. named `<dto_name>.java`."""

        if self._dto_files is None:
            self._dto_files = {}

            for full in self._iter_java_files(repo_path):
                name = os.path.basename(full)[:-len(".java")]
                self._dto_files.setdefault(name, []).append(full)

        return self._dto_files.get(dto_name, [])

    # ============================
    # MAIN ENTRY
    # ============================
    def extract_from_file(self, file_path, repo_path):

        code, tree = self._parse(file_path)
        cursor = QueryCursor(self.query)
        captures = cursor.captures(tree.root_node)

        normalized = normalize_captures(captures)
        routes_map = {}

        base_path = self.extract_base_path(tree.root_node, code)

        for node, capture_name in normalized:

            parent = get_parent(node, "method_declaration")
            if not parent:
                continue

            key = (parent.start_byte, parent.end_byte)

            # 🔥 ensure route initialized safely
            if key not in routes_map:
                routes_map[key] = {
                    "method": None,
                    "path": None,
                    "function": None
                }

            route = routes_map[key]
            text = self._get_text(node, code)

            # ============================
            # METHOD FIX
            # ============================
            if capture_name == "annotation":

                annotation = text

                if "GetMapping" in annotation:
                    route["method"] = "GET"
                elif "PostMapping" in annotation:
                    route["method"] = "POST"
                elif "PutMapping" in annotation:
                    route["method"] = "PUT"
                elif "DeleteMapping" in annotation:
                    route["method"] = "DELETE"
                elif "PatchMapping" in annotation:
                    route["method"] = "PATCH"
                elif "RequestMapping" in annotation:
                    route["method"] = "REQUEST"

            # ============================
            # PATH
            # ============================
            elif capture_name == "path":
                route["path"] = base_path + text.strip('"')

            # ============================
            # FUNCTION + FULL EXTRACTION
            # ============================
            elif capture_name == "func_name":

                route["function"] = text

                # 🔥 CORE EXTRACTION
                route["calls"] = self.extract_calls(parent, code)
                # 🔥 STEP 1: extract raw calls
                # raw_calls = self.extract_calls(parent, code)
                # self.debug(f"RAW CALLS FOR {route['function']}", raw_calls)

                # # 🔥 STEP 2: get class + method scope variables
                # class_node = self._get_parent(parent, "class_declaration")

                # self.debug(f"EXTRACTING VAR TYPES FOR {route['function']}", {
                #     "class_node": class_node.type if class_node else None,
                #     "method_node": parent.type
                # })

                # var_types = {}
                # var_types.update(self.extract_variable_types(class_node, code))  # fields

                # var_types.update(self.extract_variable_types(parent, code))      # params + locals

                # # 🔥 STEP 3: build class metadata (do once ideally, but fine here for now)
                # class_map = self.build_class_metadata(repo_path)

                # # 🔥 STEP 4: filter business calls
                # route["calls"] = self.filter_business_calls(
                #     raw_calls,
                #     var_types,
                #     class_map
                # )
                self.debug(f"FILTERED CALLS FOR {route['function']}", route["calls"])
                route["params"] = self.extract_params(parent, code, repo_path)
                base_errors = self.extract_errors(parent, code)

                validation_errors = self.derive_field_level_errors(route["params"])

                route["errors"] = base_errors + validation_errors
                route["db_ops"] = self.detect_db_ops(route["calls"])

                # ============================
                # RESPONSE FIX
                # ============================
                return_type = self.extract_return_type(parent, code)
                return_values = self.extract_return_values(parent, code)

                route["response"] = self.build_response(
                    repo_path,
                    return_type,
                    return_values
                )

        # ============================
        # 🔥 FINAL CLEANUP (VERY IMPORTANT)
        # ============================

        final_routes = []

        for route in routes_map.values():

            # skip incomplete routes
            if not route.get("function"):
                continue

            # fallback defaults
            if not route.get("method"):
                route["method"] = "GET"

            if not route.get("path"):
                route["path"] = base_path

            final_routes.append(route)

        return final_routes

    # ============================
    # 🔥 AST CALL EXTRACTION
    # ============================

    def extract_calls(self, method_node, code):
        calls = []
        stack = [method_node]

        while stack:
            node = stack.pop()

            if node.type == "method_invocation":

                identifiers = []

                for child in node.children:
                    if child.type == "identifier":
                        identifiers.append(self._get_text(child, code))

                method_name = None

                # ✅ handle all cases safely
                if len(identifiers) == 1:
                    method_name = identifiers[0]   # createStay()

                elif len(identifiers) >= 2:
                    method_name = identifiers[-1]  # always take LAST → correct

                if method_name:
                    calls.append(method_name)

            stack.extend(node.children)

        return list(set(calls))
    
    # ============================
    # 🔥 FULL GRAPH (AST)
    # ============================

    def build_full_graph(self, repo_path):

        graph = {}

        for full in self._iter_java_files(repo_path):

            try:
                code, tree = self._parse(full)
                root_node = tree.root_node

                stack = [root_node]

                while stack:
                    node = stack.pop()

                    if node.type == "method_declaration":

                        func_name = None

                        for c in node.children:
                            if c.type == "identifier":
                                func_name = self._get_text(c, code)
                                break

                        if not func_name:
                            continue

                        calls = self.extract_calls(node, code)

                        graph[func_name] = calls

                    stack.extend(node.children)

            except Exception as e:
                logger.debug("Failed to parse %s: %s", full, e)
                continue

        return graph

    # ============================
    # ROUTES
    # ============================

    def extract_all_routes(self, repo_path):

        routes = []

        for full in self._iter_java_files(repo_path):

            if "controller" not in full.lower():
                continue

            try:
                routes.extend(self.extract_from_file(full, repo_path))
            except Exception as e:
                logger.debug("Failed to extract %s: %s", full, e)
                continue

        return routes

    # ============================
    # GRAPH LOGIC
    # ============================

    def build_reverse_graph(self, graph):

        reverse = {}

        for caller, callees in graph.items():
            for callee in callees:
                reverse.setdefault(callee, []).append(caller)

        return reverse

    def find_impacted_apis(self, changed_funcs, reverse_graph, api_functions):

        impacted = set()
        visited = set()

        def dfs(func):
            if func in visited:
                return

            visited.add(func)

            if func in api_functions:
                impacted.add(func)

            for parent in reverse_graph.get(func, []):
                dfs(parent)

        for f in changed_funcs:
            dfs(f)

        return impacted

    def expand_impact(self, func, graph):
        return reachable(func, graph, include_start_on_cycle=True)

    # ============================
    # MAIN PROCESS
    # ============================

    def process_repository(self, repo_path, changed_files, commit="HEAD"):

        self._reset_caches(commit)

        self.debug("CHANGED FILES", changed_files)

        graph = self.build_full_graph(repo_path)
        self.debug("GRAPH SIZE", len(graph))

        reverse_graph = self.build_reverse_graph(graph)
        self.debug("REVERSE GRAPH KEYS", list(reverse_graph.keys())[:10])

        all_routes = self.extract_all_routes(repo_path)
        self.debug("ALL ROUTES FOUND", [r["function"] for r in all_routes])

        impacted_routes = []
        impacted_api_names = set()

        # ============================
        # 🔥 1. CONTROLLER CHANGE
        # ============================

        for file in changed_files:

            full = os.path.join(repo_path, file)

            if "controller" in file.lower() and os.path.exists(full):

                self.debug("PROCESSING CONTROLLER FILE", file)

                routes = self.extract_from_file(full, repo_path)
                self.debug("APIS IN FILE", [r["function"] for r in routes])

                try:
                    diff = subprocess.check_output(
                        ["git", "diff", "-U0", f"{commit}^", commit, "--", file],
                        cwd=repo_path,
                        stderr=subprocess.DEVNULL
                    ).decode()

                    self.debug("GIT DIFF", diff)

                except Exception as e:
                    diff = ""
                    self.debug("DIFF ERROR", str(e))

                changed_paths = set(
                    re.findall(r'"([^"]+)"', diff)
                )

                self.debug("CHANGED PATHS", changed_paths)
                for r in routes:
                    func = r.get("function")
                    path = r.get("path", "")

                    # 🔥 match by path change
                    if any(p in path for p in changed_paths):
                        self.debug("MATCHED API VIA PATH", func)
                        impacted_api_names.add(func)

        # ============================
        # 🔥 1.5 DTO CHANGE DETECTION
        # ============================

        changed_dtos = set()

        for file in changed_files:
            if file.endswith(".java") and "dto" in file.lower():
                dto_name = os.path.basename(file).replace(".java", "")
                changed_dtos.add(dto_name)
                self.debug("DTO CHANGE DETECTED", dto_name)

        if changed_dtos:
            for route in all_routes:
                for param in route.get("params", []):

                    if param.get("in") == "body":
                        param_type = param.get("type")

                        if param_type in changed_dtos:
                            self.debug("API IMPACTED BY DTO", route.get("function"))
                            impacted_api_names.add(route.get("function"))
        # ============================
        # 🔥 2. SERVICE / DAO CHANGE
        # ============================

        changed_funcs = set()

        for file in changed_files:

            if "controller" in file.lower():
                continue

            full = os.path.join(repo_path, file)

            if not os.path.exists(full):
                continue

            self.debug("PROCESSING SERVICE FILE", file)

            try:
                code, tree = self._parse(full)
                root = tree.root_node

                stack = [root]

                while stack:
                    node = stack.pop()

                    if node.type == "method_declaration":
                        for c in node.children:
                            if c.type == "identifier":
                                name = self._get_text(c, code)
                                changed_funcs.add(name)

                    stack.extend(node.children)

            except Exception as e:
                self.debug("SERVICE PARSE ERROR", str(e))
                continue

        self.debug("CHANGED FUNCS (SERVICE)", changed_funcs)

        if changed_funcs:
            api_functions = {r["function"] for r in all_routes}

            impacted_from_graph = self.find_impacted_apis(
                changed_funcs,
                reverse_graph,
                api_functions
            )

            self.debug("IMPACTED FROM GRAPH", impacted_from_graph)

            impacted_api_names.update(impacted_from_graph)

        # ============================
        # 🔥 3. BUILD RESPONSE
        # ============================

        self.debug("FINAL IMPACTED API NAMES", impacted_api_names)

        for route in all_routes:
            func = route.get("function")

            if func in impacted_api_names:
                self.debug("ADDING ROUTE", func)

                # 🔥 DIRECT CALLS (level 1)
                direct_calls = graph.get(func, [])

                # 🔥 FULL CALL CHAIN (recursive)
                full_calls = self.expand_impact(func, graph)

           # 🔥 nested tree
                call_tree = self.build_call_tree(func, graph)

                route["call_graph"] = {
                    "direct": direct_calls,
                    "full": full_calls,
                    "tree": {
                        func: call_tree
                    }
                }

                route["impact"] = full_calls

                impacted_routes.append(route)

        self.debug("FINAL ROUTES COUNT", len(impacted_routes))

        for route in impacted_routes:

            for param in route.get("params", []):

                if param.get("in") == "body":

                    dto = param.get("type")

                    # 🔥 find DTO file
                    if dto:
                        for file_path in self._find_dto_files(repo_path, dto):

                            old_code = self.get_old_file_content(repo_path, file_path)

                            if not old_code:
                                continue

                            old_schema = self.extract_dto_schema_from_code(old_code)
                            new_schema = param.get("schema", {})

                            breaking = self.detect_breaking_changes(old_schema, new_schema)

                            if breaking:
                                route["breaking_changes"] = breaking

        return impacted_routes
    # ============================
    # HELPERS
    # ============================

    def extract_base_path(self, root_node, code):
        stack = [root_node]

        while stack:
            node = stack.pop()

            if node.type == "annotation":
                text = self._get_text(node, code)

                if "RequestMapping" in text:
                    match = re.search(r'"([^"]+)"', text)
                    if match:
                        return match.group(1)

            stack.extend(node.children)

        return ""

    def _get_text(self, node, code):
        return get_text(node, code)
    
    # ============================
# DEBUG UTILITY
# ============================

    def debug(self, title, data=None):
        if data is None:
            logger.debug("%s", title)
        else:
            logger.debug("%s: %s", title, data)


    # ============================
# RESPONSE EXTRACTION
# ============================

    def extract_return_type(self, method_node, code):

        for child in method_node.children:
            if child.type in ["type_identifier", "generic_type"]:
                return self._get_text(child, code)

        return "void"


    def extract_return_values(self, method_node, code):

        returns = []

        stack = [method_node]

        while stack:
            node = stack.pop()

            if node.type == "return_statement":
                text = self._get_text(node, code)
                returns.append(text)

            stack.extend(node.children)

        return returns


    def build_response(self, repo_path, return_type, return_values):

        response = {
            "type": return_type,
            "schema": None
        }

        # 🔥 CLEAN RETURN VALUE
        clean_value = None
        if return_values:
            clean_value = return_values[0]
            clean_value = clean_value.replace("return", "").replace(";", "").strip().strip('"')

        # primitive
        if return_type in ["String", "int", "boolean"]:
            response["schema"] = clean_value
            return response

        # List<DTO>
        if "List<" in return_type:
            dto = return_type.split("<")[1].replace(">", "")
            schema = self.extract_dto_schema(repo_path, dto)

            response["schema"] = {
                "type": "array",
                "items": schema
            }
            return response

        # DTO
        schema = self.extract_dto_schema(repo_path, return_type)

        if schema:
            response["schema"] = schema

        return response

    # ============================
    # DTO SCHEMA EXTRACTION
    # ============================

    def extract_params(self, method_node, code, repo_path):

        params = []

        for child in method_node.children:
            if child.type == "formal_parameters":

                for param in child.children:
                    if param.type != "formal_parameter":
                        continue

                    param_name = None
                    param_type = None
                    default_value = None
                    required = True
                    location = "query"

                    # ============================
                    # 🔥 1. EXTRACT NAME + TYPE
                    # ============================

                    for c in param.children:

                        if c.type == "identifier":
                            param_name = self._get_text(c, code)

                        elif c.type in [
                            "type_identifier",
                            "integral_type",
                            "floating_point_type",
                            "boolean_type"
                        ]:
                            param_type = self._get_text(c, code)

                    # ============================
                    # 🔥 2. EXTRACT ANNOTATIONS (FIXED)
                    # ============================

                    annotations = []
                    stack = [param]

                    while stack:
                        node = stack.pop()

                        if node.type in ["annotation", "marker_annotation"]:
                            annotations.append(self._get_text(node, code))

                        stack.extend(node.children)

                    # ============================
                    # 🔥 3. DETERMINE LOCATION
                    # ============================

                    for annotation_text in annotations:

                        if "RequestBody" in annotation_text:
                            location = "body"

                        elif "PathVariable" in annotation_text:
                            location = "path"

                        elif "RequestParam" in annotation_text:
                            location = "query"

                            # default value
                            match = re.search(r'defaultValue\s*=\s*"([^"]+)"', annotation_text)
                            if match:
                                default_value = match.group(1)
                                required = False

                            if "required=false" in annotation_text:
                                required = False

                    # ============================
                    # 🔥 4. BUILD PARAM OBJECT
                    # ============================

                    param_obj = {
                        "name": param_name,
                        "type": param_type,
                        "in": location,
                        "required": required,
                        "default": default_value
                    }

                    # ============================
                    # 🔥 5. DTO SCHEMA (FIXED)
                    # ============================

                    if location == "body" and param_type:
                        schema = self.extract_dto_schema(repo_path, param_type)
                        if schema:
                            param_obj["schema"] = schema

                    params.append(param_obj)

        return params
        

    def extract_errors(self, method_node, code):

        errors = set()

        stack = [method_node]

        while stack:
            node = stack.pop()

            if node.type == "object_creation_expression":
                text = self._get_text(node, code)

                if "HttpStatus" in text:
                    match = re.search(r'HttpStatus\.(\w+)', text)
                    if match:
                        errors.add(match.group(1))

            stack.extend(node.children)

        return list(errors)
    
    def detect_db_ops(self, calls):

        db_ops = []
        for call in calls:
            if ".save" in call:
                db_ops.append({"type": "WRITE", "call": call})
            elif ".find" in call or ".get" in call:
                db_ops.append({"type": "READ", "call": call})

        return db_ops
    
    def extract_dto_schema(self, repo_path, dto_name):

        if dto_name in self._dto_schemas:
            return self._dto_schemas[dto_name]

        schema = {}

        for full in self._find_dto_files(repo_path, dto_name):
            try:
                code, tree = self._parse(full)
                schema.update(self._schema_from_tree(tree, code))
            except Exception as e:
                logger.debug("Failed to read DTO %s: %s", full, e)
                continue

        self._dto_schemas[dto_name] = schema

        return schema

    def derive_field_level_errors(self, params):

        errors = []

        for param in params:
            schema = param.get("schema", {})

            field_errors = []

            for field, details in schema.items():

                validations = details.get("validation", {})

                for rule in validations:
                    field_errors.append({
                        "name": field,
                        "rule": rule
                    })

            if field_errors:
                errors.append({
                    "type": "VALIDATION_ERROR",
                    "status": 400,
                    "fields": field_errors
                })

        return errors
    
    def get_old_file_content(self, repo_path, file_path):

        try:
            relative_path = os.path.relpath(file_path, repo_path)
            content = subprocess.check_output(
                ["git", "show", f"{self._commit}^:{relative_path}"],
                cwd=repo_path,
                stderr=subprocess.DEVNULL
            )

            return content

        except (subprocess.CalledProcessError, OSError):
            return None
        

    def extract_dto_schema_from_code(self, code):
        return self._schema_from_tree(self.parser.parse(code), code)

    def _schema_from_tree(self, tree, code):

        schema = {}

        root_node = tree.root_node

        stack = [root_node]

        while stack:
            node = stack.pop()

            if node.type == "field_declaration":

                field_type = None
                field_name = None
                validations = {}

                annotations = []
                inner_stack = [node]

                while inner_stack:
                    n = inner_stack.pop()

                    if n.type in ["annotation", "marker_annotation"]:
                        annotations.append(code[n.start_byte:n.end_byte].decode())

                    inner_stack.extend(n.children)

                for c in node.children:

                    if c.type in [
                        "type_identifier",
                        "integral_type",
                        "floating_point_type",
                        "boolean_type"
                    ]:
                        field_type = code[c.start_byte:c.end_byte].decode()

                    if c.type == "variable_declarator":
                        for cc in c.children:
                            if cc.type == "identifier":
                                field_name = code[cc.start_byte:cc.end_byte].decode()

                for annotation in annotations:

                    if "NotEmpty" in annotation:
                        validations["notEmpty"] = True
                        validations["required"] = True

                    elif "NotNull" in annotation:
                        validations["required"] = True

                    elif "Size" in annotation:
                        match_min = re.search(r'min\s*=\s*(\d+)', annotation)
                        match_max = re.search(r'max\s*=\s*(\d+)', annotation)

                        if match_min:
                            validations["min"] = int(match_min.group(1))
                        if match_max:
                            validations["max"] = int(match_max.group(1))

                if field_name:
                    field_schema = {"type": field_type}

                    if validations:
                        field_schema["validation"] = validations

                    schema[field_name] = field_schema

            stack.extend(node.children)

        return schema
    
    def detect_breaking_changes(self, old_schema, new_schema):

        breaking = []

        for field, new_details in new_schema.items():

            old_details = old_schema.get(field, {})

            old_validations = old_details.get("validation", {})
            new_validations = new_details.get("validation", {})

            # 🔥 removed validation
            for rule in old_validations:
                if rule not in new_validations:
                    breaking.append({
                        "type": "VALIDATION_REMOVED",
                        "field": field,
                        "rule": rule
                    })

            # 🔥 added stricter validation
            for rule in new_validations:
                if rule not in old_validations:
                    breaking.append({
                        "type": "VALIDATION_ADDED",
                        "field": field,
                        "rule": rule
                    })

        return breaking
    

    def build_call_tree(self, func, graph, visited=None):

        if visited is None:
            visited = set()

        if func in visited:
            return {}

        visited.add(func)

        tree = {}

        for call in graph.get(func, []):
            tree[call] = self.build_call_tree(call, graph, visited)

        return tree
    

    def build_class_metadata(self, repo_path):
        class_map = {}

        for full in self._iter_java_files(repo_path):

            try:
                code, tree = self._parse(full)
                root_node = tree.root_node

                class_name = None
                annotations = set()
                methods = 0
                fields = 0
                extends = None

                stack = [root_node]

                while stack:
                    node = stack.pop()

                    if node.type == "class_declaration":
                        for c in node.children:
                            if c.type == "identifier":
                                class_name = self._get_text(c, code)

                    if node.type == "annotation":
                        annotations.add(self._get_text(node, code))

                    if node.type == "method_declaration":
                        methods += 1

                    if node.type == "field_declaration":
                        fields += 1

                    if node.type == "superclass":
                        extends = self._get_text(node, code)

                    stack.extend(node.children)

                if class_name:
                    class_map[class_name] = {
                        "annotations": annotations,
                        "methods": methods,
                        "fields": fields,
                        "extends": extends
                    }

            except Exception as e:
                logger.debug("Failed to parse %s: %s", full, e)
                continue

        return class_map
    
    def extract_variable_types(self, node, code):
        var_types = {}

        stack = [node]

        while stack:
            n = stack.pop()

            if n.type in ["field_declaration", "local_variable_declaration", "formal_parameter"]:

                var_type = None
                var_name = None

                for c in n.children:

                    if c.type in ["type_identifier"]:
                        var_type = self._get_text(c, code)

                    if c.type == "variable_declarator":
                        for cc in c.children:
                            if cc.type == "identifier":
                                var_name = self._get_text(cc, code)

                    if n.type == "formal_parameter" and c.type == "identifier":
                        var_name = self._get_text(c, code)

                if var_name and var_type:
                    var_types[var_name] = var_type

            stack.extend(n.children)

        return var_types
    
    def classify_class(self, class_name, class_map):

        meta = class_map.get(class_name)

        if not meta:
            return "UNKNOWN"

        annotations = meta["annotations"]
        methods = meta["methods"]
        fields = meta["fields"]
        extends = meta["extends"]

        # 🔥 SERVICE
        for a in annotations:
            if "Service" in a:
                return "SERVICE"

        # 🔥 REPOSITORY
        for a in annotations:
            if "Repository" in a:
                return "REPOSITORY"

        if extends and "JpaRepository" in extends:
            return "REPOSITORY"

        # 🔥 DTO (structural, not naming)
        if fields > 0 and methods == 0:
            return "DTO"

        # 🔥 LOGGER (resolved via type)
        if class_name == "Logger":
            return "LOGGER"

        return "OTHER"
    
    def filter_business_calls(self, calls, var_types, class_map):

        business_calls = []

        for call in calls:

            obj = call.get("object")
            method = call.get("method")

            class_name = var_types.get(obj)

            category = self.classify_class(class_name, class_map)

            # 🔥 DROP NON-BUSINESS
            if category in ["DTO", "LOGGER"]:
                continue

            business_calls.append(method)

        return list(set(business_calls))