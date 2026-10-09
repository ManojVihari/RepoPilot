"""
REST endpoint extraction: mappings, parameters, request/response schemas,
status codes, security and everything an endpoint reaches downstream.
"""
import re
from collections import Counter
from typing import Dict, List, Optional, Set

from .analyzer import MAPPING_ANNOTATIONS, SpringAnalyzer, dedupe
from .catalog import (
    COLLECTION_TYPES, FRAMEWORK_PARAM_TYPES, MAP_TYPES, SIMPLE_TYPES, STATUS_NAME,
    VALIDATION_ANNOTATIONS, WRAPPER_TYPES,
)
from .java_index import MethodInfo, TypeInfo, TypeRef, find_annotation

MAX_SCHEMA_DEPTH = 4
MAX_LIST = 200


class EndpointExtractor:

    def __init__(self, analyzer: SpringAnalyzer):
        self.a = analyzer
        self.index = analyzer.index

    # ============================
    # DISCOVERY
    # ============================

    def controllers(self) -> List[TypeInfo]:
        return [
            t for t in self.index.types.values()
            if self.a.stereotype(t.qualified_name) in ("rest_controller", "controller")
        ]

    def extract_all(self) -> List[dict]:
        endpoints = []
        for controller in self.controllers():
            endpoints.extend(self.extract_controller(controller))

        # Docs are stored per handler; overloaded handlers and handlers mapped
        # to several HTTP methods get a stable name that includes method + path
        counts = Counter((e["module"], e["handler"]) for e in endpoints)
        for e in endpoints:
            if counts[(e["module"], e["handler"])] > 1:
                slug = re.sub(r"[^A-Za-z0-9]+", "_", e["path"]).strip("_") or "root"
                e["doc_name"] = f"{e['handler']}-{e['method']}-{slug}"
            else:
                e["doc_name"] = e["handler"]

        return endpoints

    def extract_controller(self, controller: TypeInfo) -> List[dict]:
        base_paths = self._class_paths(controller)
        endpoints = []

        for m in controller.methods:
            mapping, mapping_owner = self._find_mapping(controller, m)
            if mapping is None:
                continue

            http_methods = [MAPPING_ANNOTATIONS[mapping.name]] if MAPPING_ANNOTATIONS[mapping.name] else self.a._request_methods(mapping)
            paths = self.a.strings(mapping.arg("value", "path"), m, mapping_owner) or [""]
            full_paths = [self._join(b, p) for b in base_paths for p in paths]

            for http_method in http_methods:
                endpoints.append(self.build_endpoint(controller, m, mapping, http_method, full_paths))

        return endpoints

    def _class_paths(self, t: TypeInfo):
        for owner in [t] + self.index.supertypes(t):
            ann = owner.annotation("RequestMapping")
            if ann:
                return self.a.strings(ann.arg("value", "path"), None, owner) or [""]
        return [""]

    def _find_mapping(self, controller, m):
        """Mapping annotation on the method or on the interface method it implements."""
        for owner in [controller] + self.index.supertypes(controller):
            candidate = m if owner is controller else next(
                (x for x in owner.methods if x.name == m.name and len(x.params) == len(m.params)), None)
            if candidate is None:
                continue
            ann = next((a for a in candidate.annotations if a.name in MAPPING_ANNOTATIONS), None)
            if ann:
                return ann, owner
        return None, None

    @staticmethod
    def _join(base, path):
        joined = "/" + "/".join(p.strip("/") for p in (base, path) if p and p.strip("/"))
        return joined if joined != "" else "/"

    # ============================
    # ENDPOINT
    # ============================

    def build_endpoint(self, controller: TypeInfo, m: MethodInfo, mapping, http_method, paths) -> dict:
        a = self.a
        module_config = a.configs.get(controller.module)
        context_path = None
        if module_config:
            context_path = module_config.get("server.servlet.context-path") or module_config.get("server.context-path")

        referenced: Set[str] = set()
        params = self.params(m, controller, referenced)
        request_body = next((p for p in params if p["in"] == "body"), None)
        response = self.response(m, controller, referenced)

        # @ModelAttribute methods run before every handler of the controller
        model_attribute_ids = [
            x.id for owner in [controller] + self.index.supertypes(controller) for x in owner.methods
            if find_annotation(x.annotations, "ModelAttribute") and not any(an.name in MAPPING_ANNOTATIONS for an in x.annotations)
        ]
        reachable = a.reachable(m.id)
        chain_ids = [m.id] + reachable
        for extra in model_attribute_ids:
            for mid in [extra] + a.reachable(extra):
                if mid not in chain_ids:
                    chain_ids.append(mid)

        integrations = self._integrations(chain_ids)
        exceptions = self._exceptions(m, controller, chain_ids)
        status_codes = self._status_codes(m, chain_ids, params, exceptions)

        security = []
        for ann in m.annotations + controller.annotations:
            if ann.name in ("PreAuthorize", "PostAuthorize", "Secured", "RolesAllowed", "PermitAll", "DenyAll"):
                value = ann.arg("value")
                security.append({
                    "annotation": ann.name,
                    "expression": ", ".join(a.strings(value, m, controller)) if value else None,
                    "level": "method" if ann in m.annotations else "class",
                })
        url_rule = a.security_for(paths[0], http_method, controller.module)

        description = m.javadoc
        summary = None
        operation = find_annotation(m.annotations, "Operation", "ApiOperation")
        if operation:
            summary = a.string(operation.arg("summary", "value"), m, controller)
            description = a.string(operation.arg("description", "notes"), m, controller) or description

        errors = sorted({STATUS_NAME.get(s["code"], str(s["code"])) for s in status_codes if s["code"] >= 400})
        errors = list(errors) + self._validation_errors(params)

        direct = [a.label(c) for c in a.displayable(a.calls.get(m.id, []))]
        full = [a.label(c) for c in a.displayable(reachable)][:MAX_LIST]

        db_ops = [
            {
                "type": f.get("operation"),
                "call": f.get("call") or f.get("source"),
                "entity": f.get("entity"),
                "table": f.get("table"),
                "store": f.get("technology"),
            }
            for f in integrations.get("databases", [])
        ]

        return {
            # ---- core fields (unchanged contract) ----
            "function": m.name,
            "method": http_method,
            "path": paths[0],
            "params": params,
            "errors": errors,
            "calls": sorted(set(a.call_names.get(m.id, []))),
            "db_ops": dedupe(db_ops),
            "response": response,
            "call_graph": {"direct": direct, "full": full, "tree": {a.label(m.id): a.call_tree(m.id)}},
            "impact": full,
            # ---- location ----
            "handler": f"{controller.name}.{m.name}",
            "controller": controller.qualified_name,
            "module": controller.module,
            "file": controller.file,
            "line": m.line_start,
            "paths": paths,
            "context_path": context_path,
            "summary": summary,
            "description": description,
            "deprecated": bool(find_annotation(m.annotations, "Deprecated") or controller.has_annotation("Deprecated")),
            # ---- contract ----
            "consumes": a.strings(mapping.arg("consumes"), m, controller),
            "produces": a.strings(mapping.arg("produces"), m, controller),
            "request_body": {
                "type": request_body["type"],
                "required": request_body["required"],
                "validated": request_body.get("validated", False),
                "schema": request_body.get("schema"),
            } if request_body else None,
            "status_codes": status_codes,
            "exceptions": exceptions,
            "security": {"annotations": security, "url_rule": url_rule} if (security or url_rule) else None,
            "transactional": any(f.get("kind") == "transaction" for f in a.facts.get(m.id, [])) or None,
            # ---- downstream ----
            "integrations": integrations,
            "referenced_types": sorted(referenced),
        }

    # ============================
    # PARAMETERS
    # ============================

    def params(self, m: MethodInfo, owner: TypeInfo, referenced: Set[str]) -> List[dict]:
        out = []

        for p in m.params:
            names = {x.name for x in p.annotations}
            if p.type.name in FRAMEWORK_PARAM_TYPES or names & {"AuthenticationPrincipal", "CurrentSecurityContext"}:
                continue

            location, name, required, default = None, p.name, True, None
            ann = None

            for ann_name, loc in (("PathVariable", "path"), ("RequestParam", "query"), ("RequestHeader", "header"),
                                  ("CookieValue", "cookie"), ("RequestBody", "body"), ("RequestPart", "multipart"),
                                  ("ModelAttribute", "model"), ("MatrixVariable", "matrix")):
                ann = find_annotation(p.annotations, ann_name)
                if ann:
                    location = loc
                    break

            if ann is not None:
                explicit = self.a.string(ann.arg("name", "value"), m, owner)
                if explicit and location != "body":
                    name = explicit
                if ann.arg("required") == ("lit", False):
                    required = False
                default = self.a.string(ann.arg("defaultValue"), m, owner)
                if default is not None:
                    required = False

            if location is None:
                if p.type.name == "Pageable":
                    out.extend({"name": n, "type": t, "in": "query", "required": False, "default": None}
                               for n, t in (("page", "int"), ("size", "int"), ("sort", "String")))
                    continue
                location = "query" if self._is_simple(p.type) else "model"

            if p.type.name == "Optional":
                required = False

            entry = {"name": name, "type": str(p.type), "in": location, "required": required, "default": default}

            validation = self._validation(p.annotations, m, owner)
            if validation:
                entry["validation"] = validation
            if names & {"Valid", "Validated"}:
                entry["validated"] = True

            if location in ("body", "model", "multipart") and not self._is_simple(p.type):
                schema = self.schema_fields(p.type, owner, referenced)
                if schema:
                    entry["schema"] = schema
                elif self._collection_item(p.type) is not None:
                    entry["schema"] = {"[]": self.schema(p.type, owner, referenced)}

            out.append(entry)

        return out

    def _is_simple(self, t: TypeRef):
        if t.name in SIMPLE_TYPES or t.name in ("Optional",) and t.args and t.args[0].name in SIMPLE_TYPES:
            return True
        resolved = self.index.resolve(t.name)
        return resolved is not None and resolved.kind == "enum"

    def _validation(self, annotations, m, owner):
        rules = {}
        for ann in annotations:
            if ann.name not in VALIDATION_ANNOTATIONS or ann.name == "Valid":
                continue
            if ann.name in ("NotEmpty", "NotBlank"):
                rules["notEmpty"] = True
                rules["required"] = True
                if ann.name == "NotBlank":
                    rules["notBlank"] = True
            elif ann.name == "NotNull":
                rules["required"] = True
            elif ann.name in ("Size", "Length"):
                for key in ("min", "max"):
                    value = ann.arg(key)
                    if value and value[0] == "lit":
                        rules[key] = value[1]
            elif ann.name in ("Min", "Max", "DecimalMin", "DecimalMax"):
                value = ann.arg("value")
                key = "minimum" if "Min" in ann.name else "maximum"
                if value:
                    rules[key] = value[1] if value[0] == "lit" else self.a.string(value, m, owner)
            elif ann.name == "Pattern":
                rules["pattern"] = self.a.string(ann.arg("regexp"), m, owner)
            elif ann.name == "Email":
                rules["email"] = True
            else:
                rules[ann.name[0].lower() + ann.name[1:]] = True
        return rules

    def _validation_errors(self, params):
        field_errors = []
        for p in params:
            if p.get("in") not in ("body", "model") or not p.get("validated"):
                continue
            for field, details in (p.get("schema") or {}).items():
                for rule in (details.get("validation") or {}):
                    field_errors.append({"name": field, "rule": rule})
        if field_errors:
            return [{"type": "VALIDATION_ERROR", "status": 400, "fields": field_errors}]
        return []

    # ============================
    # SCHEMAS
    # ============================

    def _collection_item(self, t: TypeRef) -> Optional[TypeRef]:
        if t.array:
            return TypeRef(t.name, t.args, t.array - 1, t.raw.replace("[]", "", 1))
        if t.name in COLLECTION_TYPES:
            return t.args[0] if t.args else TypeRef("Object", raw="Object")
        return None

    def unwrap(self, t: TypeRef) -> TypeRef:
        while t.name in WRAPPER_TYPES and t.args:
            t = t.args[0]
        return t

    def schema(self, t: TypeRef, context: TypeInfo, referenced: Set[str], depth=0, seen=None) -> dict:
        seen = seen or set()
        t = self.unwrap(t)

        item = self._collection_item(t)
        if item is not None:
            return {"type": "array", "items": self.schema(item, context, referenced, depth, seen)}

        if t.name in MAP_TYPES:
            value = t.args[-1] if t.args else TypeRef("Object", raw="Object")
            return {"type": "map", "values": self.schema(value, context, referenced, depth, seen)}

        if t.name in SIMPLE_TYPES:
            return {"type": t.name}

        resolved = self.index.resolve(t.name, context)
        if resolved is None:
            return {"type": str(t)}

        referenced.add(resolved.qualified_name)

        if resolved.kind == "enum":
            return {"type": "enum", "name": resolved.name, "values": resolved.enum_constants}

        if depth >= MAX_SCHEMA_DEPTH or resolved.qualified_name in seen:
            return {"type": "object", "name": resolved.name, "ref": True}

        return {
            "type": "object",
            "name": resolved.name,
            "fields": self.schema_fields(t, context, referenced, depth + 1, seen | {resolved.qualified_name}),
        }

    def schema_fields(self, t: TypeRef, context: TypeInfo, referenced: Set[str], depth=0, seen=None) -> Dict[str, dict]:
        """{field: {type, validation?, schema?}} for a DTO (inherited fields included)."""
        seen = seen or set()
        resolved = self.index.resolve(self.unwrap(t).name, context)
        if resolved is None or resolved.kind == "enum":
            return {}

        referenced.add(resolved.qualified_name)
        fields = {}

        owners = list(reversed(self.index.supertypes(resolved))) + [resolved]
        for owner in owners:
            if owner.kind == "interface":
                continue
            for f in owner.fields:
                if f.is_static or find_annotation(f.annotations, "JsonIgnore", "Transient"):
                    continue

                name = f.name
                json_property = find_annotation(f.annotations, "JsonProperty", "SerializedName")
                if json_property:
                    name = self.a.string(json_property.arg("value"), None, owner) or name

                entry = {"type": str(f.type)}
                validation = self._validation(f.annotations, None, owner)
                if validation:
                    entry["validation"] = validation

                if not self._is_simple(f.type) and depth < MAX_SCHEMA_DEPTH:
                    nested = self.schema(f.type, owner, referenced, depth, seen | {resolved.qualified_name})
                    if nested.get("type") in ("object", "array", "map", "enum"):
                        entry["schema"] = nested
                elif self._is_simple(f.type):
                    enum = self.index.resolve(f.type.name, owner)
                    if enum is not None and enum.kind == "enum":
                        entry["enum"] = enum.enum_constants
                        referenced.add(enum.qualified_name)

                fields[name] = entry

        return fields

    def response(self, m: MethodInfo, owner: TypeInfo, referenced: Set[str]) -> dict:
        return_type = m.return_type or TypeRef("void", raw="void")
        body = self.unwrap(return_type)

        response = {"type": str(return_type), "body_type": str(body), "schema": None}

        if body.name == "void":
            response["schema"] = None
        elif body.name in ("String",) and m.returns and m.returns[0][0] == "str":
            response["schema"] = m.returns[0][1]
            if self.a.stereotype(owner.qualified_name) == "controller" and not find_annotation(m.annotations, "ResponseBody"):
                response["view"] = m.returns[0][1]
        elif self._is_simple(body):
            response["schema"] = {"type": body.name}
        else:
            schema = self.schema(body, owner, referenced)
            response["schema"] = schema.get("fields", schema) if schema.get("type") == "object" else schema

        if return_type.name in ("Mono", "Flux"):
            response["reactive"] = True
        if return_type.name in ("ModelAndView",) or (
            self.a.stereotype(owner.qualified_name) == "controller"
            and not find_annotation(m.annotations, "ResponseBody")
            and body.name == "String"
        ):
            response["view"] = response.get("view") or True

        return response

    # ============================
    # STATUS CODES, EXCEPTIONS, INTEGRATIONS
    # ============================

    def _status_codes(self, m, chain_ids, params, exceptions):
        codes = []

        response_status = find_annotation(m.annotations, "ResponseStatus")
        if response_status:
            code = self.a.status_code(response_status.arg("value", "code"))
            if code:
                codes.append({"code": code, "source": "@ResponseStatus"})

        codes.extend({k: v for k, v in s.items() if k != "line"} for s in self.a.statuses.get(m.id, []))

        # statuses produced deeper in the call chain (ResponseStatusException etc.)
        for mid in chain_ids[1:]:
            for s in self.a.statuses.get(mid, []):
                if s["code"] >= 400:
                    codes.append({"code": s["code"], "source": f"{s['source']} in {self.a.label(mid)}"})

        for exception in exceptions:
            if exception.get("status"):
                codes.append({"code": exception["status"], "source": f"{exception['exception']} ({exception.get('mapped_by', 'mapping')})"})

        if any(p.get("validated") or p.get("validation") for p in params):
            codes.append({"code": 400, "source": "bean validation"})

        if not any(200 <= c["code"] < 300 for c in codes):
            codes.append({"code": 200, "source": "default"})

        for c in codes:
            c["reason"] = STATUS_NAME.get(c["code"])

        return sorted(dedupe(codes), key=lambda c: (c["code"], c["source"]))

    def _exceptions(self, m, controller, chain_ids):
        out = []
        for mid in chain_ids:
            owner = self.a.method_owner.get(mid)
            method = self.a.methods.get(mid)
            names = list(self.a.thrown.get(mid, []))
            if mid == m.id and method:
                names += [t.name for t in method.throws]
            for name in names:
                mapping = self.a.exception_mapping(name, owner or controller, controller)
                out.append({
                    "exception": name.rsplit(".", 1)[-1],
                    "thrown_in": self.a.label(mid),
                    # Spring answers 500 for exceptions nothing maps
                    "status": (mapping.get("status") if mapping else None) or (None if mapping else 500),
                    "mapped_by": (mapping.get("handler") or mapping.get("source")) if mapping else "unhandled",
                })
        return dedupe(out)[:MAX_LIST]

    def _integrations(self, chain_ids) -> dict:
        groups = {
            "database": "databases", "cache": "caches", "messaging": "messaging",
            "http": "external_apis", "event": "events", "email": "email",
            "storage": "storage", "search": "search", "transaction": "transactions",
            "async": "async", "resilience": "resilience",
        }
        out: Dict[str, list] = {}
        for mid in chain_ids:
            for fact in self.a.facts.get(mid, []):
                group = groups.get(fact.get("kind"), "other")
                cleaned = {k: v for k, v in fact.items() if k not in ("module", "file")}
                out.setdefault(group, []).append(cleaned)
        return {k: dedupe(v)[:MAX_LIST] for k, v in out.items()}
