"""
Semantic analysis of a Spring code base on top of JavaIndex.

Produces, per method:
  * resolved call edges (Class.method -> Class.method, through interfaces)
  * integration facts: database, cache, messaging, outbound HTTP, email,
    storage, internal events
  * HTTP statuses it can produce and exceptions it throws

and application-wide facts: component stereotypes, injected dependencies,
repositories, entities, message listeners, scheduled jobs, security rules and
exception -> status mappings.
"""
import json
import logging
import re
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .catalog import (
    CLIENT_TYPES, HTTP_METHOD_BY_REST_TEMPLATE_CALL, HTTP_STATUS, HTTP_VERBS,
    KNOWN_EXCEPTION_STATUS, REPOSITORY_BASES,
    RESPONSE_ENTITY_BUILDERS, STEREOTYPES,
)
from .java_index import Annotation, Chain, JavaIndex, MethodInfo, TypeInfo, TypeRef, find_annotation

logger = logging.getLogger(__name__)

MAPPING_ANNOTATIONS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": None,
}

PASSTHROUGH_CALLS = {
    "create", "of", "valueOf", "fromUriString", "fromHttpUrl", "fromPath", "toUri",
    "toUriString", "trim", "toString", "requireNonNull", "uri", "parse",
}

MAX_CLOSURE = 3000

STATUS_CARRYING_EXCEPTIONS = {
    "ResponseStatusException", "HttpClientErrorException", "HttpServerErrorException", "ErrorResponseException",
}


def snake_case(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def short_method(method: MethodInfo):
    return f"{method.owner.rsplit('.', 1)[-1]}.{method.name}"


def parse_sql(sql: str):
    """(operation, tables) of a SQL / JPQL statement."""
    text = re.sub(r"\s+", " ", sql or "").strip()
    if not text:
        return None, []

    first = text.split(" ", 1)[0].upper()
    operation = {
        "SELECT": "READ", "WITH": "READ", "FROM": "READ", "INSERT": "WRITE", "UPDATE": "WRITE",
        "MERGE": "WRITE", "UPSERT": "WRITE", "REPLACE": "WRITE", "DELETE": "DELETE",
        "CALL": "CALL", "EXEC": "CALL", "EXECUTE": "CALL", "TRUNCATE": "DELETE",
    }.get(first, "QUERY")

    tables = []
    for match in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE|MERGE INTO|TRUNCATE TABLE|TABLE)\s+([\w.\"`\[\]]+)", text, re.I):
        name = match.group(1).strip("\"`[]")
        # JPQL path joins (o.pets) are not tables
        if "." in name and re.match(r"^[a-z]\w*\.\w+$", name) and not re.search(r"\bFROM\s+" + re.escape(name), text, re.I):
            continue
        if name.upper() not in ("SELECT", "WHERE", "SET", "VALUES", "(") and name not in tables:
            tables.append(name)

    return operation, tables


def repository_operation(method_name):
    name = method_name
    if re.match(r"(save|insert|persist|merge|update|upsert|create|store|add|flush)", name):
        return "WRITE"
    if re.match(r"(delete|remove|purge|truncate)", name):
        return "DELETE"
    if re.match(r"(find|get|read|query|search|stream|count|exists|load|fetch|list|select|lookup|retrieve)", name):
        return "READ"
    return "CALL"


def redis_operation(call_name):
    name = call_name.lower()
    if name.startswith(("get", "has", "members", "range", "size", "keys", "exists", "entries", "values", "ismember", "scan", "multiget", "rank", "score", "index")):
        return "READ"
    if name.startswith(("delete", "remove", "pop", "expire", "unlink", "evict", "invalidate", "trim", "left pop", "rightpop", "leftpop")):
        return "DELETE"
    return "WRITE"


class SpringAnalyzer:

    def __init__(self, index: JavaIndex, modules: Dict[str, dict], configs: Dict[str, object], mybatis_mappers=None):
        self.index = index
        self.modules = modules
        self.configs = configs
        self.mybatis_xml = mybatis_mappers or {}
        self.mybatis_mappers: Set[str] = set()

        self.app_names = {}
        for module_dir, config in configs.items():
            name = config.get("spring.application.name") if config else None
            if name:
                self.app_names[str(name)] = module_dir
        for module_dir, info in modules.items():
            self.app_names.setdefault(info.get("name") or module_dir, module_dir)

        self.methods: Dict[str, MethodInfo] = {}
        self.method_owner: Dict[str, TypeInfo] = {}
        self.stereotypes: Dict[str, Tuple[str, str]] = {}
        self.repositories: Dict[str, dict] = {}
        self.entities: Dict[str, dict] = {}
        self.feign_clients: Dict[str, dict] = {}
        self.exception_status: Dict[str, dict] = {}
        self.event_listeners: Dict[str, List[str]] = {}
        self.listeners: List[dict] = []
        self.scheduled: List[dict] = []
        self.client_base_urls: Dict[str, List[dict]] = {}
        self.security = {"mechanisms": set(), "rules": [], "configured_in": []}

        # per-method results
        self.calls: Dict[str, List[str]] = {}
        self.call_names: Dict[str, List[str]] = {}
        self.facts: Dict[str, List[dict]] = {}
        self.statuses: Dict[str, List[dict]] = {}
        self.thrown: Dict[str, List[str]] = {}
        self.referenced_types: Dict[str, Set[str]] = {}

    # ============================
    # ENTRY POINT
    # ============================

    def analyze(self):
        for t in self.index.types.values():
            for m in t.methods + t.constructors:
                self.methods[m.id] = m
                self.method_owner[m.id] = t

        self._find_mybatis_mappers()

        for t in self.index.types.values():
            self.stereotypes[t.qualified_name] = self._classify(t)

        for t in self.index.types.values():
            self._collect_entity(t)

        for t in self.index.types.values():
            self._collect_repository(t)
            self._collect_mybatis_mapper(t)
            self._collect_feign_client(t)
            self._collect_exception_status(t)

        for t in self.index.types.values():
            self._collect_advice(t)
            for m in t.methods:
                self._collect_method_annotations(t, m)

        self.collect_client_definitions()

        for mid, m in self.methods.items():
            self._analyze_method(m, self.method_owner[mid])

        # synchronous internal events: publisher -> @EventListener methods
        for mid, facts in self.facts.items():
            for f in facts:
                if f.get("kind") == "event" and f.get("event_type"):
                    for listener in self.event_listeners.get(f["event_type"], []):
                        if listener not in self.calls[mid]:
                            self.calls[mid].append(listener)

        self.collect_security()
        self.security["mechanisms"] = sorted(self.security["mechanisms"])

    # ============================
    # CLASSIFICATION
    # ============================

    def _classify(self, t: TypeInfo):
        names = {a.name for a in t.annotations}

        if t.qualified_name in self.mybatis_mappers:
            return "repository", "data"
        if "Mapper" in names and t.imports.get("Mapper", "").startswith("org.mapstruct"):
            return "mapper", "service"

        for annotation, stereotype, layer in STEREOTYPES:
            if annotation in names:
                if stereotype == "controller" and "ResponseBody" in names:
                    return "rest_controller", "web"
                return stereotype, layer

        if t.kind == "interface" and self._repository_base(t):
            return "repository", "data"

        if any(find_annotation(m.annotations, "KafkaListener", "RabbitListener", "JmsListener", "SqsListener", "StreamListener") for m in t.methods):
            return "message_listener", "integration"

        if t.kind == "enum":
            return "enum", "domain"

        if t.kind == "annotation":
            return "annotation", "other"

        if self.index.is_subtype_of(t, "Exception") or self.index.is_subtype_of(t, "RuntimeException") or t.name.endswith("Exception"):
            return "exception", "domain"

        if t.kind in ("class", "record") and t.fields and not [m for m in t.methods if not self._is_accessor(m)]:
            return "dto", "domain"

        if t.name.endswith(("Dto", "DTO", "Request", "Response", "Payload", "Event", "Message", "Command", "Resource", "View", "Form")):
            return "dto", "domain"

        return "class", "other"

    @staticmethod
    def _is_accessor(m: MethodInfo):
        return bool(re.match(r"^(get|set|is|has|with|equals|hashCode|toString|builder|of|from|compareTo)", m.name)) or m.is_constructor

    def stereotype(self, qualified_name):
        return self.stereotypes.get(qualified_name, ("class", "other"))[0]

    def _repository_base(self, t: TypeInfo) -> Optional[TypeRef]:
        for ref in self.index.ancestor_refs(t):
            if ref.name in REPOSITORY_BASES:
                return ref
        return None

    def _module_store_kind(self, module):
        deps = {d.get("technology") for d in self.modules.get(module, {}).get("dependencies", [])}
        if any(d and "MongoDB" in d for d in deps):
            return "mongodb"
        if "R2DBC" in deps:
            return "r2dbc"
        if "Spring Data JDBC" in deps:
            return "jdbc"
        if any(d and "Redis" in d for d in deps):
            return "redis"
        return "jpa"

    # ============================
    # DOMAIN: repositories, entities
    # ============================

    def _collect_repository(self, t: TypeInfo):
        if t.kind != "interface":
            return

        base = self._repository_base(t)
        if not base:
            return

        # the repository's own declaration may carry the generics instead of the base
        generic = next((r for r in t.extends if r.args), None) or base
        entity_ref = generic.args[0] if generic.args else None
        entity = self.index.resolve(entity_ref.name, t) if entity_ref else None
        store = REPOSITORY_BASES.get(base.name) or self._module_store_kind(t.module)

        queries = []
        for m in t.methods:
            query = find_annotation(m.annotations, "Query")
            if query:
                text = self.string(query.arg("value"), None, t)
                native = query.arg("nativeQuery") == ("lit", True)
                operation, tables = parse_sql(text or "")
                if (text or "").lstrip().startswith("{"):
                    # MongoDB JSON filter
                    operation = "DELETE" if query.arg("delete") == ("lit", True) else "READ"
                    tables = []
                if find_annotation(m.annotations, "Modifying") and operation == "READ":
                    operation = "WRITE"
                queries.append({
                    "method": m.name,
                    "query": text,
                    "native": native,
                    "operation": operation,
                    "tables": [self._table_for(x, t) for x in tables],
                })

        self.repositories[t.qualified_name] = {
            "name": t.name,
            "qualified_name": t.qualified_name,
            "module": t.module,
            "file": t.file,
            "store": store,
            "base": base.name,
            "entity": entity.name if entity else (entity_ref.name if entity_ref else None),
            "entity_qualified_name": entity.qualified_name if entity else None,
            "id_type": str(generic.args[1]) if len(generic.args) > 1 else None,
            "methods": [m.name for m in t.methods],
            "custom_queries": queries,
        }

    def _find_mybatis_mappers(self):
        """Mapper interfaces: XML namespace, @Mapper (MyBatis) or a @MapperScan package."""
        scan_packages = []
        for t in self.index.types.values():
            scan = t.annotation("MapperScan")
            if scan:
                scan_packages += self.strings(scan.arg("value", "basePackages"), None, t)

        for t in self.index.types.values():
            if t.kind != "interface":
                continue
            mapper_ann = t.annotation("Mapper")
            is_mybatis_annotation = mapper_ann is not None and not t.imports.get("Mapper", "").startswith("org.mapstruct")
            annotated_sql = any(find_annotation(m.annotations, "Select", "Insert", "Update", "Delete") for m in t.methods)
            in_scan = any(t.package == p or t.package.startswith(p + ".") for p in scan_packages)

            if t.qualified_name in self.mybatis_xml or is_mybatis_annotation or annotated_sql or in_scan:
                self.mybatis_mappers.add(t.qualified_name)

    def _collect_mybatis_mapper(self, t: TypeInfo):
        if t.qualified_name not in self.mybatis_mappers:
            return

        xml = self.mybatis_xml.get(t.qualified_name, {})
        statements = dict(xml.get("statements", {}))

        for m in t.methods:
            ann = find_annotation(m.annotations, "Select", "Insert", "Update", "Delete")
            if ann:
                statements[m.name] = {
                    "operation": {"Select": "READ", "Insert": "WRITE", "Update": "WRITE", "Delete": "DELETE"}[ann.name],
                    "sql": " ".join(self.strings(ann.arg("value"), m, t)),
                }

        queries = []
        for method_name, statement in sorted(statements.items()):
            _, tables = parse_sql(statement["sql"])
            queries.append({
                "method": method_name,
                "query": statement["sql"][:2000],
                "native": True,
                "operation": statement["operation"],
                "tables": tables,
            })

        entity = (xml.get("entity") or "").rsplit(".", 1)[-1] or None

        self.repositories[t.qualified_name] = {
            "name": t.name,
            "qualified_name": t.qualified_name,
            "module": t.module,
            "file": t.file,
            "store": "mybatis",
            "base": "MyBatis mapper",
            "entity": entity,
            "entity_qualified_name": xml.get("entity"),
            "id_type": None,
            "mapper_xml": xml.get("file"),
            "methods": [m.name for m in t.methods],
            "custom_queries": queries,
        }

    def _table_for(self, name, context):
        """Entity name in JPQL -> table name; plain table names pass through."""
        entity = self.index.resolve(name, context)
        if entity and entity.qualified_name in self.entities:
            return self.entities[entity.qualified_name]["table"]
        for e in self.entities.values():
            if e["name"] == name or e.get("jpa_name") == name:
                return e["table"]
        return name

    def _collect_entity(self, t: TypeInfo):
        ann = t.annotation("Entity", "Document", "Table", "RedisHash", "MappedSuperclass", "Embeddable", "Node")
        if not ann or t.kind not in ("class", "record"):
            return

        table_ann = t.annotation("Table", "Document", "RedisHash", "Node")
        table = None
        if table_ann:
            table = self.string(table_ann.arg("name", "value", "collection", "indexName"), None, t)
        entity_ann = t.annotation("Entity")
        jpa_name = self.string(entity_ann.arg("name"), None, t) if entity_ann else None

        document_import = t.imports.get("Document", "")
        if t.has_annotation("Document") and ("elasticsearch" in document_import or t.annotation("Document").arg("indexName")):
            store = "elasticsearch"
            default_table = t.name[0].lower() + t.name[1:]
        elif t.has_annotation("Document") and "couchbase" in document_import:
            store = "couchbase"
            default_table = t.name[0].lower() + t.name[1:]
        elif t.has_annotation("Document", "RedisHash"):
            store = "mongodb" if t.has_annotation("Document") else "redis"
            default_table = t.name[0].lower() + t.name[1:]
        else:
            store = "jpa" if t.has_annotation("Entity", "MappedSuperclass", "Embeddable") else self._module_store_kind(t.module)
            default_table = snake_case(jpa_name or t.name)

        fields, relationships = [], []
        owners = [t] + [s for s in self.index.supertypes(t) if s.has_annotation("MappedSuperclass", "Entity", "Document")]

        for owner in owners:
            for f in owner.fields:
                if f.is_static or find_annotation(f.annotations, "Transient"):
                    continue

                column = find_annotation(f.annotations, "Column", "Field", "JoinColumn")
                relation = find_annotation(f.annotations, "OneToMany", "ManyToOne", "OneToOne", "ManyToMany", "DBRef", "DocumentReference", "Relationship", "ElementCollection")
                entry = {"name": f.name, "type": str(f.type)}

                if find_annotation(f.annotations, "Id", "EmbeddedId"):
                    entry["id"] = True
                    generated = find_annotation(f.annotations, "GeneratedValue")
                    if generated:
                        entry["generated"] = self._enum_name(generated.arg("strategy")) or "AUTO"
                if column:
                    name = self.string(column.arg("name", "value"), None, owner)
                    if name:
                        entry["column"] = name
                    nullable = column.arg("nullable")
                    if nullable == ("lit", False):
                        entry["nullable"] = False
                    if column.arg("unique") == ("lit", True):
                        entry["unique"] = True
                    length = column.arg("length")
                    if length and length[0] == "lit":
                        entry["length"] = length[1]
                if relation:
                    target_ref = f.type.args[-1] if f.type.args else f.type
                    target = self.index.resolve(target_ref.name, owner)
                    rel = {
                        "field": f.name,
                        "type": relation.name,
                        "target": target.name if target else target_ref.name,
                    }
                    mapped_by = self.string(relation.arg("mappedBy"), None, owner)
                    if mapped_by:
                        rel["mapped_by"] = mapped_by
                    cascade = relation.arg("cascade")
                    if cascade:
                        rel["cascade"] = self._enum_name(cascade)
                    fetch = relation.arg("fetch")
                    if fetch:
                        rel["fetch"] = self._enum_name(fetch)
                    relationships.append(rel)
                    entry["relationship"] = relation.name

                validations = [a.name for a in f.annotations if a.name in ("NotNull", "NotEmpty", "NotBlank", "Size", "Email", "Pattern", "Min", "Max", "Digits")]
                if validations:
                    entry["validation"] = validations

                fields.append(entry)

        self.entities[t.qualified_name] = {
            "name": t.name,
            "qualified_name": t.qualified_name,
            "module": t.module,
            "file": t.file,
            "store": store,
            "kind": ann.name,
            "table": table or default_table,
            "table_inferred": table is None,
            "jpa_name": jpa_name,
            "extends": [str(r) for r in t.extends],
            "fields": fields,
            "relationships": relationships,
        }

    def _collect_feign_client(self, t: TypeInfo):
        ann = t.annotation("FeignClient")
        if not ann:
            return

        name = self.string(ann.arg("name", "value", "serviceId"), None, t)
        url = self.string(ann.arg("url"), None, t)
        base_path = self.string(ann.arg("path"), None, t) or ""

        class_mapping = t.annotation("RequestMapping")
        if class_mapping:
            base_path += self.string(class_mapping.arg("value", "path"), None, t) or ""

        fallback = ann.arg("fallback", "fallbackFactory")

        operations = {}
        for m in t.methods:
            http_method, path = self._mapping_of(m, t)
            if http_method is None:
                request_line = find_annotation(m.annotations, "RequestLine")
                if request_line:
                    line = self.string(request_line.arg("value"), None, t) or ""
                    parts = line.split(" ", 1)
                    http_method, path = parts[0], parts[1] if len(parts) > 1 else ""
            if http_method is not None:
                operations[m.name] = {"http_method": http_method, "path": base_path + (path or "")}

        self.feign_clients[t.qualified_name] = {
            "name": t.name,
            "qualified_name": t.qualified_name,
            "module": t.module,
            "file": t.file,
            "service": name,
            "url": url,
            "target_module": self.app_names.get(name) if name else None,
            "fallback": fallback[1] if fallback and fallback[0] == "class" else None,
            "operations": operations,
        }

    def _mapping_of(self, m: MethodInfo, owner: TypeInfo):
        """(HTTP method, path) of a mapping annotation on m, or (None, None)."""
        for a in m.annotations:
            if a.name in MAPPING_ANNOTATIONS:
                http_method = MAPPING_ANNOTATIONS[a.name]
                if http_method is None:
                    http_method = self._request_methods(a)[0]
                paths = self.strings(a.arg("value", "path"), m, owner) or [""]
                return http_method, paths[0]
        return None, None

    def _request_methods(self, a: Annotation):
        method_arg = a.arg("method")
        if method_arg is None:
            return ["ANY"]
        values = method_arg[1] if method_arg[0] == "list" else [method_arg]
        names = [self._enum_name(v) for v in values]
        return [n for n in names if n] or ["ANY"]

    @staticmethod
    def _enum_name(expr):
        if not expr:
            return None
        if expr[0] == "ref":
            return expr[1].rsplit(".", 1)[-1]
        if expr[0] == "list":
            return ",".join(filter(None, (SpringAnalyzer._enum_name(e) for e in expr[1])))
        if expr[0] == "str":
            return expr[1]
        return None

    def _collect_exception_status(self, t: TypeInfo):
        ann = t.annotation("ResponseStatus")
        if ann:
            code = self.status_code(ann.arg("value", "code"))
            if code:
                self.exception_status[t.qualified_name] = {
                    "exception": t.name, "status": code, "source": "@ResponseStatus",
                    "reason": self.string(ann.arg("reason"), None, t),
                }

    def _collect_advice(self, t: TypeInfo):
        if not t.has_annotation("ControllerAdvice", "RestControllerAdvice") and self.stereotype(t.qualified_name) not in ("rest_controller", "controller"):
            return

        for m in t.methods:
            handler = find_annotation(m.annotations, "ExceptionHandler")
            if not handler:
                continue

            types = [e[1] for e in self._flatten_list(handler.arg("value")) if e[0] == "class"]
            if not types:
                types = [p.type.name for p in m.params if p.type.name.endswith(("Exception", "Error", "Throwable"))]

            status = None
            response_status = find_annotation(m.annotations, "ResponseStatus")
            if response_status:
                status = self.status_code(response_status.arg("value", "code"))

            for type_name in types:
                exception = self.index.resolve(type_name, t)
                key = exception.qualified_name if exception else type_name
                self.exception_status[key] = {
                    "exception": type_name,
                    "status": status,
                    "handler": f"{t.name}.{m.name}",
                    "handler_id": m.id,
                    "global": t.has_annotation("ControllerAdvice", "RestControllerAdvice"),
                    "source": "@ExceptionHandler",
                }

    @staticmethod
    def _flatten_list(expr):
        if not expr:
            return []
        return expr[1] if expr[0] == "list" else [expr]

    def _collect_method_annotations(self, t: TypeInfo, m: MethodInfo):
        source = {"handler": f"{t.name}.{m.name}", "method_id": m.id, "module": t.module, "file": t.file, "line": m.line_start}

        class_kafka = t.annotation("KafkaListener")
        kafka = find_annotation(m.annotations, "KafkaListener") or (class_kafka if find_annotation(m.annotations, "KafkaHandler") else None)
        if kafka:
            self.listeners.append({
                "technology": "kafka",
                "destinations": self.strings(kafka.arg("topics", "value", "topicPattern"), m, t),
                "group": self.string(kafka.arg("groupId", "id"), m, t),
                "payload_type": self._payload_param(m),
                **source,
            })

        rabbit = find_annotation(m.annotations, "RabbitListener") or (t.annotation("RabbitListener") if find_annotation(m.annotations, "RabbitHandler") else None)
        if rabbit:
            queues = self.strings(rabbit.arg("queues", "value", "queuesToDeclare"), m, t)
            bindings = []
            for b in self._flatten_list(rabbit.arg("bindings")):
                if b[0] == "ann":
                    qb = b[1]
                    queue = qb.arg("value")
                    exchange = qb.arg("exchange")
                    bindings.append({
                        "queue": self.string(queue[1].arg("value", "name"), m, t) if queue and queue[0] == "ann" else None,
                        "exchange": self.string(exchange[1].arg("value", "name"), m, t) if exchange and exchange[0] == "ann" else None,
                        "routing_keys": self.strings(qb.arg("key"), m, t),
                    })
            self.listeners.append({
                "technology": "rabbitmq",
                "destinations": queues + [b["queue"] for b in bindings if b.get("queue")],
                "bindings": bindings,
                "payload_type": self._payload_param(m),
                **source,
            })

        for name, tech, keys in (
            ("JmsListener", "jms", ("destination",)),
            ("SqsListener", "sqs", ("value", "queueNames")),
            ("StreamListener", "stream", ("value", "target")),
            ("PulsarListener", "pulsar", ("topics", "value")),
        ):
            ann = find_annotation(m.annotations, name)
            if ann:
                self.listeners.append({
                    "technology": tech,
                    "destinations": self.strings(ann.arg(*keys), m, t),
                    "payload_type": self._payload_param(m),
                    **source,
                })

        send_to = find_annotation(m.annotations, "SendTo")
        if send_to:
            self._add_fact(m, {
                "kind": "messaging", "direction": "publish", "technology": "reply",
                "destination": ", ".join(self.strings(send_to.arg("value"), m, t)),
                "line": m.line_start,
            })

        event = find_annotation(m.annotations, "EventListener", "TransactionalEventListener")
        if event:
            types = [e[1] for e in self._flatten_list(event.arg("value", "classes")) if e[0] == "class"]
            types = types or [p.type.name for p in m.params[:1]]
            for type_name in types:
                self.event_listeners.setdefault(type_name, []).append(m.id)
            self.listeners.append({
                "technology": "spring-event",
                "destinations": types,
                "transactional_phase": self._enum_name(event.arg("phase")) if event.name == "TransactionalEventListener" else None,
                "async": bool(find_annotation(m.annotations, "Async")),
                **source,
            })

        scheduled = find_annotation(m.annotations, "Scheduled")
        if scheduled:
            schedule = {}
            for key in ("cron", "fixedRate", "fixedDelay", "initialDelay", "fixedRateString", "fixedDelayString", "zone"):
                value = scheduled.arg(key)
                if value is not None:
                    schedule[key] = self.string(value, m, t) if value[0] != "lit" else value[1]
            self.scheduled.append({"schedule": schedule, **source})

        # functional Spring Cloud Stream beans: Consumer<T> / Function<T,R> / Supplier<T>
        if find_annotation(m.annotations, "Bean") and m.return_type and m.return_type.name in ("Consumer", "Function", "Supplier", "BiFunction"):
            bindings = self._config_value(t.module, "spring.cloud.stream.bindings")
            kind = m.return_type.name
            in_binding = self._stream_destination(t.module, f"{m.name}-in-0")
            out_binding = self._stream_destination(t.module, f"{m.name}-out-0")
            if kind in ("Consumer", "Function", "BiFunction") and (in_binding or bindings is None):
                self.listeners.append({
                    "technology": "stream",
                    "destinations": [in_binding or f"{m.name}-in-0"],
                    "payload_type": str(m.return_type.args[0]) if m.return_type.args else None,
                    **source,
                })
            if kind in ("Function", "Supplier"):
                self._add_fact(m, {
                    "kind": "messaging", "direction": "publish", "technology": "stream",
                    "destination": out_binding or f"{m.name}-out-0",
                    "payload_type": str(m.return_type.args[-1]) if m.return_type.args else None,
                    "line": m.line_start,
                })

    def _payload_param(self, m: MethodInfo):
        for p in m.params:
            if find_annotation(p.annotations, "Payload") or not p.annotations:
                if p.type.name not in ("Acknowledgment", "Channel", "Message", "ConsumerRecord", "MessageHeaders"):
                    return str(p.type)
                if p.type.args:
                    return str(p.type.args[-1])
        return str(m.params[0].type) if m.params else None

    def _config_value(self, module, key):
        config = self.configs.get(module)
        return config.get(key) if config else None

    def _stream_destination(self, module, binding):
        return self._config_value(module, f"spring.cloud.stream.bindings.{binding}.destination")

    # ============================
    # EXPRESSION EVALUATION
    # ============================

    def string(self, expr, method: Optional[MethodInfo], owner: TypeInfo, depth=0):
        """Best-effort string value of an expression ({x} for unknown parts)."""
        value = self._string(expr, method, owner, depth)
        if value is None:
            return None
        config = self.configs.get(owner.module) if owner else None
        return config.resolve(value) if config else value

    def strings(self, expr, method, owner):
        if expr is None:
            return []
        if expr[0] == "list":
            return [s for s in (self.string(e, method, owner) for e in expr[1]) if s is not None]
        value = self.string(expr, method, owner)
        return [value] if value is not None else []

    def _string(self, expr, method, owner, depth):
        if expr is None or depth > 8:
            return None

        kind = expr[0]

        if kind == "str":
            return expr[1]

        if kind == "lit":
            return None if expr[1] is None else str(expr[1]).lower() if isinstance(expr[1], bool) else str(expr[1])

        if kind == "concat":
            parts = []
            for part in expr[1]:
                value = self._string(part, method, owner, depth + 1)
                parts.append(value if value is not None else self._placeholder(part))
            return "".join(parts)

        if kind == "ref":
            return self._ref_string(expr[1], method, owner, depth)

        if kind == "call":
            name, args = expr[1], expr[2]
            if name in PASSTHROUGH_CALLS and args:
                return self._string(args[0], method, owner, depth + 1)
            if name == "format" and args:
                fmt = self._string(args[0], method, owner, depth + 1)
                return re.sub(r"%[sd]", "{}", fmt) if fmt else None
            receiver = expr[3] or ""
            enum_value = self._enum_getter(receiver, name, owner, depth)
            if enum_value is not None:
                return enum_value
            discovered = re.search(r"(?:getInstances|choose|getNextServerFromEureka|getInstance)\(\s*\"([^\"]+)\"", receiver)
            if discovered and name in ("getUri", "getUrl", "toString", "getHost", "getHomePageUrl", "uri"):
                # service discovery lookup: DiscoveryClient / LoadBalancerClient
                return f"lb://{discovered.group(1)}"
            if receiver in ("", "this") and not args:
                local = next((x for x in owner.methods if x.name == name and not x.params), None) if owner else None
                if local is not None and local.returns:
                    return self._string(local.returns[0], local, owner, depth + 1)
            if name in ("concat", "append") and args and expr[3]:
                left = self._ref_string(expr[3], method, owner, depth) if re.fullmatch(r"[\w.]+", expr[3]) else None
                right = self._string(args[0], method, owner, depth + 1)
                if left is not None and right is not None:
                    return left + right
            return None

        return None

    def _enum_getter(self, receiver, getter, owner, depth):
        """Enum.CONSTANT.getX() -> the constant's constructor argument bound to field x."""
        if not owner or "." not in receiver or not getter.startswith(("get", "is")) or depth > 6:
            return None
        type_name, constant = receiver.rsplit(".", 1)
        enum = self.index.resolve(type_name, owner)
        if enum is None or enum.kind != "enum" or constant not in enum.enum_args:
            return None
        field_name = getter[3:] if getter.startswith("get") else getter[2:]
        field_name = field_name[:1].lower() + field_name[1:]
        for constructor in enum.constructors:
            names = [p.name for p in constructor.params]
            if field_name in names and len(enum.enum_args[constant]) == len(names):
                return self._string(enum.enum_args[constant][names.index(field_name)], None, enum, depth + 1)
        return None

    def string_or_placeholder(self, expr, method, owner):
        value = self.string(expr, method, owner)
        if value is None and expr is not None:
            return self._placeholder(expr)
        return value

    def _placeholder(self, expr):
        if expr[0] == "ref":
            return "{" + expr[1].rsplit(".", 1)[-1] + "}"
        if expr[0] == "call":
            return "{" + expr[1] + "()}"
        return "{?}"

    def _ref_string(self, name, method, owner, depth):
        if owner is None:
            return None

        if "." not in name:
            if method is not None:
                if name in method.locals:
                    init = method.locals[name][1]
                    return self._string(init, method, owner, depth + 1) if init else None
                if any(p.name == name for p in method.params):
                    return None

            found = self.index.find_field(owner, name)
            if found:
                return self._field_string(*found, depth)
            return None

        head, tail = name.rsplit(".", 1)
        target = self.index.resolve(head, owner)
        if target:
            f = target.field_named(tail)
            if f:
                return self._field_string(f, target, depth)
        return None

    def _field_string(self, f, owner, depth):
        value_ann = find_annotation(f.annotations, "Value")
        if value_ann:
            return self._string(value_ann.arg("value"), None, owner, depth + 1)
        if f.init is not None:
            return self._string(f.init, None, owner, depth + 1)
        return None

    def status_code(self, expr) -> Optional[int]:
        if not expr:
            return None
        if expr[0] == "lit" and isinstance(expr[1], int):
            return expr[1]
        if expr[0] == "ref":
            return HTTP_STATUS.get(expr[1].rsplit(".", 1)[-1])
        if expr[0] == "call" and expr[1] in ("valueOf", "resolve") and expr[2]:
            return self.status_code(expr[2][0])
        return None

    # ============================
    # TYPE RESOLUTION
    # ============================

    def _var_type(self, name, method: MethodInfo, owner: TypeInfo) -> Optional[TypeRef]:
        if name in method.locals:
            return method.locals[name][0]
        for p in method.params:
            if p.name == name:
                return p.type
        found = self.index.find_field(owner, name)
        if found:
            return found[0].type
        return None

    def _root_type(self, root, method: MethodInfo, owner: TypeInfo):
        """(TypeRef or None, is_static) for the receiver of a chain."""
        kind = root[0]

        if kind in ("none", "this"):
            return TypeRef(owner.name, raw=owner.qualified_name), False

        if kind == "super":
            return (owner.extends[0] if owner.extends else None), False

        if kind == "field":
            found = self.index.find_field(owner, root[1])
            return (found[0].type if found else None), False

        if kind == "name":
            var_type = self._var_type(root[1], method, owner)
            if var_type is not None:
                return var_type, False
            if root[1][:1].isupper():
                return TypeRef(root[1], raw=root[1]), True
            return None, False

        if kind == "ref":
            parts = root[1].split(".")
            var_type = self._var_type(parts[0], method, owner)
            if var_type is not None and len(parts) == 2:
                target = self.index.resolve(var_type.name, owner)
                if target:
                    found = self.index.find_field(target, parts[1])
                    if found:
                        return found[0].type, False
                return None, False
            target = self.index.resolve(parts[-2] if len(parts) > 1 else parts[0], owner)
            if target and len(parts) > 1:
                f = target.field_named(parts[-1])
                if f:
                    return f.type, False
            if parts[-1][:1].isupper():
                return TypeRef(parts[-1], raw=root[1]), True
            return None, False

        if kind in ("new", "cast", "type"):
            return TypeRef(root[1], raw=root[2] if len(root) > 2 else root[1]), kind == "type"

        return None, False

    def _expr_type(self, expr, method: MethodInfo, owner: TypeInfo) -> Optional[TypeRef]:
        if not expr:
            return None
        if expr[0] == "ref" and "." not in expr[1]:
            return self._var_type(expr[1], method, owner)
        if expr[0] == "new":
            return TypeRef(expr[1], raw=expr[1])
        if expr[0] == "str" or expr[0] == "concat":
            return TypeRef("String", raw="String")
        return None

    def client_kind(self, type_ref: Optional[TypeRef]):
        if type_ref is None:
            return None
        raw = type_ref.raw or type_ref.name
        for prefix, kind in (("WebClient.", "web_client"), ("RestClient.", "rest_client"),
                             ("Request.Builder", "okhttp"), ("HttpRequest.", "java_http_client"),
                             ("RestTemplateBuilder", "rest_template")):
            if raw.startswith(prefix) or type_ref.name == prefix.rstrip("."):
                return kind
        return CLIENT_TYPES.get(type_ref.name)

    # ============================
    # METHOD ANALYSIS
    # ============================

    def _add_fact(self, m: MethodInfo, fact):
        owner = self.method_owner.get(m.id)
        fact.setdefault("source", short_method(m))
        if owner is not None:
            fact.setdefault("module", owner.module)
            fact.setdefault("file", owner.file)
        self.facts.setdefault(m.id, []).append({k: v for k, v in fact.items() if v not in (None, "", [], {})})

    def _analyze_method(self, m: MethodInfo, owner: TypeInfo):
        self.calls.setdefault(m.id, [])
        self.facts.setdefault(m.id, [])
        self.statuses.setdefault(m.id, [])
        self.call_names[m.id] = []
        thrown = []

        for chain in m.chains:
            self.call_names[m.id].extend(c.name for c in chain.calls)
            self._analyze_chain(chain, m, owner)

        for creation in m.creations:
            self._analyze_creation(creation, m, owner)

        thrown_refs = list(m.thrown)

        # Optional.orElseThrow(() -> new X()) / orElseThrow(X::new)
        for chain in m.chains:
            for call in chain.calls:
                if call.name == "orElseThrow" and call.args:
                    text = str(call.args[0][1]) if call.args[0][0] in ("lambda", "expr") else ""
                    for name in re.findall(r"new\s+([\w.]+)\s*\(", text) + re.findall(r"([\w.]+)::new", text):
                        thrown_refs.append(TypeRef(name.rsplit(".", 1)[-1], raw=name))
                elif call.name == "orElseThrow" and not call.args:
                    thrown_refs.append(TypeRef("NoSuchElementException", raw="NoSuchElementException"))

        for t in thrown_refs:
            if t.name in STATUS_CARRYING_EXCEPTIONS:
                continue  # status recorded from the constructor argument
            resolved = self.index.resolve(t.name, owner)
            thrown.append(resolved.qualified_name if resolved else t.name)

        self.thrown[m.id] = thrown

        # Spring cache abstraction
        cache_config = owner.annotation("CacheConfig")
        default_caches = self.strings(cache_config.arg("cacheNames", "value"), m, owner) if cache_config else []
        for a in m.annotations:
            operation = {"Cacheable": "READ_THROUGH", "CachePut": "WRITE", "CacheEvict": "EVICT"}.get(a.name)
            if operation:
                self._add_fact(m, {
                    "kind": "cache", "technology": "spring-cache", "operation": operation,
                    "cache_names": self.strings(a.arg("value", "cacheNames"), m, owner) or default_caches,
                    "key": self.string(a.arg("key"), m, owner),
                    "all_entries": True if a.arg("allEntries") == ("lit", True) else None,
                    "line": m.line_start,
                })
            elif a.name == "Caching":
                for inner_key, op in (("cacheable", "READ_THROUGH"), ("put", "WRITE"), ("evict", "EVICT")):
                    for inner in self._flatten_list(a.arg(inner_key)):
                        if inner[0] == "ann":
                            self._add_fact(m, {
                                "kind": "cache", "technology": "spring-cache", "operation": op,
                                "cache_names": self.strings(inner[1].arg("value", "cacheNames"), m, owner),
                                "line": m.line_start,
                            })

        if find_annotation(m.annotations, "Transactional") or owner.has_annotation("Transactional"):
            self._add_fact(m, {"kind": "transaction", "technology": "spring-tx", "line": m.line_start,
                               "read_only": True if self._transactional_read_only(m, owner) else None})

        if find_annotation(m.annotations, "Async"):
            self._add_fact(m, {"kind": "async", "technology": "spring-async", "line": m.line_start})

        for name in ("CircuitBreaker", "Retry", "RateLimiter", "Bulkhead", "TimeLimiter", "HystrixCommand", "Retryable"):
            a = find_annotation(m.annotations, name)
            if a:
                self._add_fact(m, {
                    "kind": "resilience", "technology": name,
                    "name": self.string(a.arg("name"), m, owner),
                    "fallback": self.string(a.arg("fallbackMethod"), m, owner),
                    "line": m.line_start,
                })

    def _transactional_read_only(self, m, owner):
        a = find_annotation(m.annotations, "Transactional") or owner.annotation("Transactional")
        return a is not None and a.arg("readOnly") == ("lit", True)

    def _analyze_chain(self, chain: Chain, m: MethodInfo, owner: TypeInfo):
        root_type, is_static = self._root_type(chain.root, m, owner)
        kind = self.client_kind(root_type)

        # ---- resolve calls through indexed types ----
        current = root_type
        context = owner
        for call in chain.calls:
            target = self.index.resolve(current.name, context) if current is not None else None
            if target is None:
                break

            argc = None if call.args is None else len(call.args)
            candidates = self.index.find_methods(target, call.name, argc)

            for candidate in candidates:
                if candidate.id != m.id and candidate.id not in self.calls[m.id]:
                    if self.stereotype(candidate.owner) not in ("dto", "entity", "document", "embeddable", "enum", "exception"):
                        self.calls[m.id].append(candidate.id)

            self._repository_fact(target, call, m, owner)
            self._feign_fact(target, call, m)

            if not candidates or candidates[0].return_type is None:
                break
            context = self.method_owner.get(candidates[0].id, context)
            current = candidates[0].return_type

        # ---- integrations by client type ----
        if kind:
            handler = getattr(self, f"_client_{kind}", None)
            if handler:
                handler(chain, m, owner, root_type)
            else:
                self._generic_client(kind, chain, m, owner)

        # ---- status codes ----
        self._chain_statuses(chain, m, owner, root_type)

    def _analyze_creation(self, creation, m: MethodInfo, owner: TypeInfo):
        name = creation.type.name

        if name in STATUS_CARRYING_EXCEPTIONS:
            code = self.status_code(creation.args[0]) if creation.args else None
            if code:
                self.statuses[m.id].append({"code": code, "source": f"new {name}", "line": creation.line})
            return

        if name == "ResponseEntity" and creation.args:
            for arg in creation.args[1:]:
                code = self.status_code(arg)
                if code:
                    self.statuses[m.id].append({"code": code, "source": "new ResponseEntity", "line": creation.line})
            return

        if name in ("HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "HttpHead"):
            url = self.string(creation.args[0], m, owner) if creation.args else None
            self._http_fact(m, "apache_http_client", name[4:].upper(), url, None, creation.line)

    # ---------- data ----------

    def _repository_fact(self, target: TypeInfo, call, m, owner):
        repo = self.repositories.get(target.qualified_name)
        if repo is None:
            return

        query = next((q for q in repo["custom_queries"] if q["method"] == call.name), None)
        operation = query["operation"] if query else repository_operation(call.name)
        if query is None and repo["store"] == "mybatis":
            # e.g. MyBatis Generator *Example methods: table from any statement of the mapper
            tables = sorted({t for q in repo["custom_queries"] for t in q["tables"]})
            query = {"tables": tables, "query": None} if tables else None
        entity_info = self.entities.get(repo.get("entity_qualified_name") or "")

        self._add_fact(m, {
            "kind": "database",
            "technology": repo["store"],
            "operation": operation,
            "repository": repo["name"],
            "call": f"{repo['name']}.{call.name}",
            "entity": repo.get("entity"),
            "table": (query or {}).get("tables") or ([entity_info["table"]] if entity_info else None),
            "query": (query or {}).get("query"),
            "line": call.line,
        })

    def _client_jdbc(self, chain, m, owner, root_type):
        for i, call in enumerate(chain.calls):
            if call.args is None:
                continue
            if call.name in ("sql",) or re.match(r"(query|update|batchUpdate|execute)", call.name):
                if not call.args:
                    continue
                sql = self.string(call.args[0], m, owner)
                operation, tables = parse_sql(sql or "")
                if sql is None and call.name.startswith("update"):
                    operation = "WRITE"
                self._add_fact(m, {
                    "kind": "database", "technology": "jdbc",
                    "operation": operation or ("WRITE" if call.name.startswith(("update", "batch")) else "READ"),
                    "call": f"{root_type.name}.{call.name}", "query": sql, "table": tables,
                    "line": call.line,
                })
                return

    def _client_jpa_entity_manager(self, chain, m, owner, root_type):
        for call in chain.calls:
            args = call.args or []
            if call.name in ("createQuery", "createNativeQuery") and args:
                sql = self.string(args[0], m, owner)
                operation, tables = parse_sql(sql or "")
                self._add_fact(m, {
                    "kind": "database", "technology": "jpa", "operation": operation,
                    "call": f"EntityManager.{call.name}", "query": sql,
                    "table": [self._table_for(t, owner) for t in tables], "line": call.line,
                })
                return
            if call.name in ("persist", "merge", "remove", "find", "getReference", "refresh", "detach"):
                entity = next((a[1] for a in args if a[0] == "class"), None)
                if entity is None and args:
                    arg_type = self._expr_type(args[0], m, owner)
                    entity = arg_type.name if arg_type else None
                self._add_fact(m, {
                    "kind": "database", "technology": "jpa",
                    "operation": {"persist": "WRITE", "merge": "WRITE", "remove": "DELETE"}.get(call.name, "READ"),
                    "call": f"EntityManager.{call.name}", "entity": entity,
                    "table": [self._table_for(entity, owner)] if entity else None, "line": call.line,
                })
                return

    _client_hibernate_session = _client_jpa_entity_manager

    def _client_mongo_template(self, chain, m, owner, root_type):
        call = chain.calls[0] if chain.root[0] != "none" else None
        call = next((c for c in chain.calls if c.args is not None), call)
        if call is None:
            return
        args = call.args or []
        entity = next((a[1] for a in args if a[0] == "class"), None)
        if entity is None and args and call.name in ("save", "insert", "remove"):
            arg_type = self._expr_type(args[0], m, owner)
            entity = arg_type.name if arg_type else None
        collection = next((self.string(a, m, owner) for a in args if a[0] == "str"), None)
        self._add_fact(m, {
            "kind": "database", "technology": "mongodb",
            "operation": repository_operation(call.name if not call.name.startswith("aggregate") else "find"),
            "call": f"MongoTemplate.{call.name}", "entity": entity,
            "table": [collection or self._table_for(entity, owner)] if (collection or entity) else None,
            "line": call.line,
        })

    def _generic_client(self, kind, chain, m, owner):
        first = chain.calls[0]
        if kind == "email" and not any(c.name.startswith("send") for c in chain.calls):
            return
        category = {
            "r2dbc": "database", "dynamodb": "database", "elasticsearch": "search",
            "hazelcast": "cache", "local_cache": "cache", "email": "email", "s3": "storage",
            "gcs": "storage", "azure_blob": "storage", "websocket": "messaging",
            "pubsub": "messaging",
        }.get(kind)
        if category is None:
            return

        fact = {"kind": category, "technology": kind, "call": ".".join(c.name for c in chain.calls), "line": first.line}

        if category == "storage":
            first_args = chain.calls[0].args or []
            if first_args and first_args[0][0] in ("str", "ref", "concat"):
                value = self.string(first_args[0], m, owner)
                if value:
                    fact["bucket"] = value
            if "bucket" not in fact:
                # SDK v2 request builders: PutObjectRequest.builder().bucket(x)
                bucket = self._aws_destination(m, owner, ("bucket", "bucketName", "withBucketName", "container", "blobContainerName"))
                if bucket:
                    fact["bucket"] = bucket
            fact["operation"] = "READ" if re.search(r"get|list|download|head", first.name, re.I) else "DELETE" if "delete" in first.name.lower() else "WRITE"
        elif category in ("database", "search", "cache"):
            fact["operation"] = repository_operation(first.name) if category != "cache" else redis_operation(first.name)
        elif category == "messaging":
            fact["direction"] = "publish"
            fact["destination"] = self.string(first.args[0], m, owner) if first.args else None

        self._add_fact(m, fact)

    # ---------- cache ----------

    def _client_redis(self, chain, m, owner, root_type):
        names = [c.name for c in chain.calls]
        last = chain.calls[-1]
        first = names[0]

        # RedisTemplate setup (serializers, connection factory) is not data access
        if root_type is not None and root_type.name.endswith("RedisTemplate") and (
            first.startswith("set") or first == "afterPropertiesSet"
            or (first.startswith("get") and first.endswith(("Serializer", "ConnectionFactory")))
        ):
            return

        if last.name in ("convertAndSend", "publish"):
            self._add_fact(m, {
                "kind": "messaging", "direction": "publish", "technology": "redis-pubsub",
                "destination": self.string(last.args[0], m, owner) if last.args else None,
                "line": last.line,
            })
            return

        key_call = next((c for c in reversed(chain.calls) if c.args), None)
        key = self.string_or_placeholder(key_call.args[0], m, owner) if key_call else None

        self._add_fact(m, {
            "kind": "cache", "technology": "redis",
            "operation": redis_operation(last.name),
            "command": ".".join(names),
            "key": key, "line": last.line,
        })

    def _client_cache_manager(self, chain, m, owner, root_type):
        get_cache = next((c for c in chain.calls if c.name == "getCache"), None)
        name = self.string(get_cache.args[0], m, owner) if get_cache and get_cache.args else None
        last = chain.calls[-1]
        self._add_fact(m, {
            "kind": "cache", "technology": "spring-cache",
            "operation": "EVICT" if last.name in ("evict", "clear", "invalidate", "evictIfPresent") else redis_operation(last.name),
            "cache_names": [name] if name else None, "command": ".".join(c.name for c in chain.calls),
            "line": last.line,
        })

    # ---------- messaging ----------

    def _client_kafka(self, chain, m, owner, root_type):
        for call in chain.calls:
            if call.name not in ("send", "sendDefault", "sendOffsetsToTransaction") or call.args is None:
                continue
            args = call.args
            topic = None
            if call.name == "send" and args:
                if args[0][0] == "new" and args[0][1] in ("ProducerRecord", "GenericMessage") and args[0][2]:
                    topic = self.string(args[0][2][0], m, owner)
                else:
                    topic = self.string_or_placeholder(args[0], m, owner)
            payload = self._expr_type(args[-1], m, owner) if args else None
            if payload is None and root_type is not None and root_type.args:
                payload = root_type.args[-1]
            self._add_fact(m, {
                "kind": "messaging", "direction": "publish", "technology": "kafka",
                "destination": topic or ("<default topic>" if call.name == "sendDefault" else None),
                "payload_type": str(payload) if payload else None, "line": call.line,
            })
            return

    def _client_rabbitmq(self, chain, m, owner, root_type):
        for call in chain.calls:
            if not call.name.startswith(("convertAndSend", "send", "convertSendAndReceive", "sendAndReceive")) or not call.args:
                continue
            args = call.args
            exchange = routing_key = None
            if len(args) >= 3:
                exchange, routing_key = self.string_or_placeholder(args[0], m, owner), self.string_or_placeholder(args[1], m, owner)
            elif len(args) == 2:
                routing_key = self.string_or_placeholder(args[0], m, owner)
            payload = self._expr_type(args[-1], m, owner)
            self._add_fact(m, {
                "kind": "messaging", "direction": "publish", "technology": "rabbitmq",
                "exchange": exchange, "routing_key": routing_key,
                "destination": exchange or routing_key,
                "payload_type": str(payload) if payload else None, "line": call.line,
            })
            return

    def _client_jms(self, chain, m, owner, root_type):
        for call in chain.calls:
            if call.name.startswith(("convertAndSend", "send")) and call.args:
                destination = self.string(call.args[0], m, owner) if len(call.args) > 1 else None
                payload = self._expr_type(call.args[-1], m, owner)
                self._add_fact(m, {
                    "kind": "messaging", "direction": "publish", "technology": "jms",
                    "destination": destination, "payload_type": str(payload) if payload else None,
                    "line": call.line,
                })
                return

    def _client_stream(self, chain, m, owner, root_type):
        for call in chain.calls:
            if call.name == "send" and call.args:
                binding = self.string(call.args[0], m, owner)
                payload = self._expr_type(call.args[-1], m, owner)
                self._add_fact(m, {
                    "kind": "messaging", "direction": "publish", "technology": "stream",
                    "binding": binding,
                    "destination": self._stream_destination(owner.module, binding) or binding,
                    "payload_type": str(payload) if payload else None, "line": call.line,
                })
                return

    def _aws_destination(self, m, owner, names):
        for chain in m.chains:
            for call in chain.calls:
                if call.name in names and call.args:
                    value = self.string(call.args[0], m, owner)
                    if value:
                        return value
        return None

    def _client_sqs(self, chain, m, owner, root_type):
        call = next((c for c in chain.calls if c.name.startswith(("send", "convertAndSend"))), None)
        if call is None:
            return
        destination = None
        if call.args and call.args[0][0] in ("str", "ref", "concat") and len(call.args) > 1:
            destination = self.string(call.args[0], m, owner)
        destination = destination or self._aws_destination(m, owner, ("queueUrl", "queueName", "queue"))
        self._add_fact(m, {
            "kind": "messaging", "direction": "publish", "technology": "sqs",
            "destination": destination, "line": call.line,
        })

    def _client_sns(self, chain, m, owner, root_type):
        call = next((c for c in chain.calls if c.name in ("publish", "send", "sendNotification", "convertAndSend")), None)
        if call is None:
            return
        destination = None
        if call.args and call.args[0][0] in ("str", "ref", "concat") and len(call.args) > 1:
            destination = self.string(call.args[0], m, owner)
        destination = destination or self._aws_destination(m, owner, ("topicArn", "topicName"))
        self._add_fact(m, {
            "kind": "messaging", "direction": "publish", "technology": "sns",
            "destination": destination, "line": call.line,
        })

    def _client_event(self, chain, m, owner, root_type):
        call = next((c for c in chain.calls if c.name == "publishEvent"), None)
        if call is None or not call.args:
            return
        event_type = self._expr_type(call.args[0], m, owner)
        self._add_fact(m, {
            "kind": "event", "technology": "spring-event", "direction": "publish",
            "event_type": event_type.name if event_type else None, "line": call.line,
        })

    # ---------- http ----------

    def _http_fact(self, m, client, http_method, url, response_type, line, extra=None):
        fact = {
            "kind": "http", "client": client, "http_method": http_method, "url": url,
            "response_type": response_type, "line": line,
        }
        fact.update(self._url_target(url))
        fact.update(extra or {})
        self._add_fact(m, fact)

    def _url_target(self, url):
        if not url:
            return {}
        match = re.match(r"^(?:(\w+)://)?(\{[^}]+\}|[^/{}:]+)(?::(\d+))?", url)
        if not match or not match.group(1):
            return {}
        host = match.group(2)
        out = {"host": host}
        if host in self.app_names:
            out["target_module"] = self.app_names[host]
            out["target_service"] = host
        elif match.group(1) == "lb":
            out["target_service"] = host
        return out

    def _response_type(self, args):
        for a in args or []:
            if a[0] == "class":
                return a[1]
            if a[0] == "new" and a[1] == "ParameterizedTypeReference":
                return "ParameterizedTypeReference"
        return None

    def _client_rest_template(self, chain, m, owner, root_type):
        for call in chain.calls:
            args = call.args or []
            if call.name in HTTP_METHOD_BY_REST_TEMPLATE_CALL:
                http_method = HTTP_METHOD_BY_REST_TEMPLATE_CALL[call.name]
            elif call.name in ("exchange", "execute") and len(args) > 1:
                http_method = self._enum_name(args[1]) if args[1][0] == "ref" else None
                if args[0][0] == "new" and args[0][1] == "RequestEntity":
                    http_method = None
            else:
                continue
            url = self.string_or_placeholder(args[0], m, owner) if args else None
            self._http_fact(m, "rest_template", http_method, url, self._response_type(args), call.line)
            return

    def _fluent_http(self, client, chain, m, owner, root_type):
        verb = url = response = None
        for call in chain.calls:
            args = call.args or []
            lname = call.name.lower()
            if lname in HTTP_VERBS and not verb and (client != "okhttp" or call.name in ("get", "post", "put", "delete", "patch", "head")):
                verb = call.name.upper()
            elif call.name == "method" and args:
                verb = self._enum_name(args[0]) or self.string(args[0], m, owner)
            elif call.name in ("uri", "url") and args:
                if args[0][0] == "lambda":
                    paths = re.findall(r"\.path\(\s*\"([^\"]*)\"", args[0][1])
                    url = "".join(paths) or None
                else:
                    url = self.string_or_placeholder(args[0], m, owner)
            elif call.name in ("bodyToMono", "bodyToFlux", "body", "toEntity", "toEntityList", "retrieve"):
                response = self._response_type(args) or response
        if verb or url:
            extra = {}
            if client in ("web_client", "rest_client") and url and not re.match(r"^\w+://", url):
                base = self._base_url_for(chain, m, owner, client)
                if base:
                    extra["base_url"] = base
                    extra.update(self._url_target(base))
            self._http_fact(m, client, verb, url, response, chain.calls[0].line, extra)

    def _client_web_client(self, chain, m, owner, root_type):
        names = {c.name for c in chain.calls}
        if "baseUrl" in names and ("build" in names or "builder" in names):
            return  # client definition, handled in _collect_client_definitions
        self._fluent_http("web_client", chain, m, owner, root_type)

    def _client_rest_client(self, chain, m, owner, root_type):
        names = {c.name for c in chain.calls}
        if "baseUrl" in names and ("build" in names or "builder" in names):
            return
        self._fluent_http("rest_client", chain, m, owner, root_type)

    def _client_java_http_client(self, chain, m, owner, root_type):
        if any(c.name in ("send", "sendAsync") for c in chain.calls):
            return
        self._fluent_http("java_http_client", chain, m, owner, root_type)

    def _client_okhttp(self, chain, m, owner, root_type):
        if chain.root[0] == "new" or any(c.name == "url" for c in chain.calls):
            self._fluent_http("okhttp", chain, m, owner, root_type)

    def _base_url_for(self, chain, m, owner, client):
        definitions = self.client_base_urls.get(owner.module, [])
        definitions = [d for d in definitions if d["client"] == client]
        if not definitions:
            return None
        if chain.root[0] in ("field", "name"):
            name = chain.root[1].lower()
            for d in definitions:
                if d.get("bean") and d["bean"].lower() == name:
                    return d["base_url"]
        return definitions[0]["base_url"] if len(definitions) == 1 else None

    def collect_client_definitions(self):
        """WebClient/RestClient builders with baseUrl (usually @Bean methods)."""
        for m in self.methods.values():
            owner = self.method_owner[m.id]
            for chain in m.chains:
                names = [c.name for c in chain.calls]
                if "baseUrl" not in names:
                    continue
                root_type, _ = self._root_type(chain.root, m, owner)
                client = self.client_kind(root_type) or ("web_client" if chain.root[1:2] == ("WebClient",) else None)
                if client not in ("web_client", "rest_client"):
                    continue
                call = next(c for c in chain.calls if c.name == "baseUrl")
                base = self.string(call.args[0], m, owner) if call.args else None
                if base:
                    self.client_base_urls.setdefault(owner.module, []).append({
                        "client": client, "base_url": base,
                        "bean": m.name if find_annotation(m.annotations, "Bean") else None,
                        "defined_in": short_method(m),
                    })

    def _feign_fact(self, target: TypeInfo, call, m):
        client = self.feign_clients.get(target.qualified_name)
        if client is None:
            return
        operation = client["operations"].get(call.name, {})
        url = client.get("url")
        fact = {
            "kind": "http", "client": "feign", "feign_client": client["name"],
            "http_method": operation.get("http_method"),
            "url": url.rstrip("/") + operation.get("path", "") if url else operation.get("path"),
            "service": client.get("service"),
            "call": f"{client['name']}.{call.name}", "line": call.line,
        }
        if url:
            # explicit url: the target is that host, the name is only a label
            fact.update(self._url_target(url))
        else:
            fact["target_service"] = client.get("service")
            fact["target_module"] = client.get("target_module")
        self._add_fact(m, fact)

    # ---------- statuses & security ----------

    def _chain_statuses(self, chain, m, owner, root_type):
        if root_type is None:
            return
        if root_type.name == "ResponseEntity":
            for call in chain.calls:
                if call.name in RESPONSE_ENTITY_BUILDERS:
                    self.statuses[m.id].append({"code": RESPONSE_ENTITY_BUILDERS[call.name], "source": f"ResponseEntity.{call.name}", "line": call.line})
                elif call.name in ("status", "of") and call.args:
                    code = self.status_code(call.args[0])
                    if code:
                        self.statuses[m.id].append({"code": code, "source": "ResponseEntity.status", "line": call.line})

    def collect_security(self):
        matchers = ("requestMatchers", "antMatchers", "mvcMatchers", "pathMatchers", "regexMatchers", "securityMatcher")
        rules = {"permitAll", "authenticated", "hasRole", "hasAnyRole", "hasAuthority", "hasAnyAuthority",
                 "denyAll", "access", "anonymous", "fullyAuthenticated", "rememberMe", "hasIpAddress", "hasScope"}
        mechanisms = {
            "oauth2ResourceServer": "oauth2-resource-server", "jwt": "jwt", "oauth2Login": "oauth2-login",
            "httpBasic": "http-basic", "formLogin": "form-login", "saml2Login": "saml2",
            "x509": "x509", "oauth2Client": "oauth2-client", "rememberMe": "remember-me",
        }

        for m in self.methods.values():
            owner = self.method_owner[m.id]
            for chain in m.chains:
                names = [c.name for c in chain.calls]
                found = False
                for name, mechanism in mechanisms.items():
                    if name in names:
                        self.security["mechanisms"].add(mechanism)
                        found = True
                root_name = chain.root[1] if len(chain.root) > 1 else ""
                if "disable" in names and ("csrf" in names or "csrf" in str(root_name).lower()):
                    self.security["csrf_disabled"] = True
                    found = True

                patterns, http_method = None, None
                for call in chain.calls:
                    if call.name in matchers or call.name in ("anyRequest", "anyExchange"):
                        args = call.args or []
                        http_method = next((self._enum_name(a) for a in args if a[0] == "ref" and "HttpMethod" in a[1]), None)
                        patterns = [self.string(a, m, owner) for a in args if a[0] in ("str", "ref", "concat") and "HttpMethod" not in str(a[1])]
                        patterns = [p for p in patterns if p] or (["/**"] if call.name in ("anyRequest", "anyExchange") else [])
                    elif call.name in rules and patterns is not None:
                        args = [self.string(a, m, owner) for a in (call.args or [])]
                        self.security["rules"].append({
                            "patterns": patterns, "http_method": http_method,
                            "access": call.name, "roles": [a for a in args if a],
                            "module": owner.module, "defined_in": short_method(m),
                        })
                        patterns, found = None, True

                if found and short_method(m) not in self.security["configured_in"]:
                    self.security["configured_in"].append(short_method(m))

        for t in self.index.types.values():
            if t.has_annotation("EnableWebSecurity", "EnableResourceServer", "EnableAuthorizationServer", "EnableMethodSecurity", "EnableGlobalMethodSecurity", "EnableOAuth2Sso"):
                self.security["mechanisms"].add({
                    "EnableResourceServer": "oauth2-resource-server",
                    "EnableAuthorizationServer": "oauth2-authorization-server",
                    "EnableMethodSecurity": "method-security",
                    "EnableGlobalMethodSecurity": "method-security",
                    "EnableOAuth2Sso": "oauth2-sso",
                }.get(next(a.name for a in t.annotations if a.name.startswith("Enable")), "spring-security"))

    def security_for(self, path, http_method, module):
        """First matching URL rule (Spring evaluates rules in order)."""
        for rule in self.security["rules"]:
            if rule["module"] != module:
                continue
            if rule.get("http_method") and http_method not in (rule["http_method"], "ANY"):
                continue
            if any(self._ant_match(p, path) for p in rule["patterns"]):
                return {k: rule[k] for k in ("patterns", "access", "roles", "defined_in") if rule.get(k)}
        return None

    @staticmethod
    def _ant_match(pattern, path):
        regex = re.escape(pattern).replace(r"/\*\*", r"(/.*)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        regex = re.sub(r"\\\{[^}]*\\\}", "[^/]+", regex)
        normalized = re.sub(r"\{[^}]+\}", "x", path)
        return re.fullmatch(regex, normalized) is not None or re.fullmatch(regex, path) is not None

    # ============================
    # CLOSURE
    # ============================

    def reachable(self, method_id) -> List[str]:
        """Methods reachable from method_id (excluding itself), BFS order."""
        seen, order = {method_id}, []
        queue = deque(self.calls.get(method_id, []))

        while queue and len(order) < MAX_CLOSURE:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            order.append(current)
            queue.extend(self.calls.get(current, []))

        return order

    def call_tree(self, method_id, depth=0, seen=None, max_depth=6):
        seen = seen or set()
        if depth >= max_depth or method_id in seen:
            return {}
        seen = seen | {method_id}
        return {
            self.label(c): self.call_tree(c, depth + 1, seen, max_depth)
            for c in self.displayable(self.calls.get(method_id, []))
        }

    def displayable(self, method_ids):
        """Drop abstract interface methods when an implementation is also listed."""
        implemented = {
            self.methods[mid].name for mid in method_ids
            if mid in self.methods and "abstract" not in self.methods[mid].modifiers
        }
        return [
            mid for mid in method_ids
            if not (mid in self.methods and "abstract" in self.methods[mid].modifiers and self.methods[mid].name in implemented)
        ]

    def label(self, method_id):
        m = self.methods.get(method_id)
        return short_method(m) if m else method_id

    def exception_mapping(self, exception_name, context: TypeInfo, controller: TypeInfo):
        """Status mapping for an exception type: own @ResponseStatus, handler, ancestors."""
        exception = self.index.resolve(exception_name.rsplit(".", 1)[-1], context) if exception_name else None
        chain = [exception] + self.index.supertypes(exception) if exception else []
        names = [t.qualified_name for t in chain] or [exception_name]

        for key in names:
            mapping = self.exception_status.get(key)
            if mapping and (mapping.get("global", True) or mapping.get("handler", "").startswith(controller.name + ".")):
                return mapping

        for t in chain or []:
            for ref in t.extends:
                if ref.name in KNOWN_EXCEPTION_STATUS:
                    return {"exception": exception_name, "status": KNOWN_EXCEPTION_STATUS[ref.name], "source": "spring-default"}

        simple = exception_name.rsplit(".", 1)[-1]
        if simple in KNOWN_EXCEPTION_STATUS:
            return {"exception": simple, "status": KNOWN_EXCEPTION_STATUS[simple], "source": "spring-default"}
        for key, mapping in self.exception_status.items():
            if key.rsplit(".", 1)[-1] == simple:
                return mapping
        return None
