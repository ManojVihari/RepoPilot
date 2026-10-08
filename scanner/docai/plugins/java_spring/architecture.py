"""
Application-wide model: modules, components, data model, integrations and an
architecture graph (nodes + edges) ready to be rendered.
"""
from collections import defaultdict
from typing import Dict, List

from .analyzer import SpringAnalyzer, dedupe
from .build_files import public_module
from .catalog import INJECTION_ANNOTATIONS
from .config_files import derive_infrastructure, masked
from .java_index import TypeInfo, find_annotation

COMPONENT_STEREOTYPES = {
    "rest_controller", "controller", "controller_advice", "feign_client", "repository",
    "service", "configuration", "application", "configuration_properties", "mapper",
    "component", "message_listener",
}

INJECTING_CONSTRUCTOR_ANNOTATIONS = {"RequiredArgsConstructor", "AllArgsConstructor"}


class ArchitectureBuilder:

    def __init__(self, analyzer: SpringAnalyzer, endpoints: List[dict]):
        self.a = analyzer
        self.index = analyzer.index
        self.endpoints = endpoints

    def build(self) -> dict:
        modules = self._modules()
        components = self._components()

        return {
            "summary": self._summary(modules, components),
            "modules": modules,
            "components": components,
            "data_model": {
                "entities": sorted(self.a.entities.values(), key=lambda e: e["name"]),
                "repositories": sorted(self.a.repositories.values(), key=lambda r: r["name"]),
                "tables": self._tables(),
            },
            "endpoints": [self._endpoint_summary(e) for e in self.endpoints],
            "integrations": self._integrations(),
            "security": self._security(),
            "exception_handling": sorted(
                (dict(v, exception=v["exception"]) for v in self.a.exception_status.values()),
                key=lambda v: v["exception"],
            ),
            "graph": self._graph(modules, components),
        }

    # ============================
    # MODULES
    # ============================

    def _modules(self) -> List[dict]:
        out = []
        counts = defaultdict(lambda: defaultdict(int))
        for t in self.index.types.values():
            counts[t.module][self.a.stereotype(t.qualified_name)] += 1

        endpoints_per_module = defaultdict(int)
        for e in self.endpoints:
            endpoints_per_module[e["module"]] += 1

        for module_dir, info in sorted(self.a.modules.items()):
            config = self.a.configs.get(module_dir)
            infra = derive_infrastructure(config) if config else {}
            main_class = next(
                (t for t in self.index.types.values() if t.module == module_dir and t.has_annotation("SpringBootApplication")),
                None,
            )

            module = public_module(info)
            module["dependencies"] = [d for d in info.get("dependencies", []) if d.get("scope") != "test"]
            module["technologies"] = sorted({
                f"{d['category']}: {d['technology']}" for d in module["dependencies"]
                if d.get("category") and d["category"] not in ("test", "code-generation")
            })
            module["runtime"] = infra
            module["config_files"] = config.files if config else []
            module["properties"] = masked(config.default) if config else {}
            module["profile_properties"] = {p: masked(v) for p, v in (config.profiles.items() if config else [])}
            module["main_class"] = main_class.qualified_name if main_class else None
            module["is_spring_boot_app"] = main_class is not None
            module["type_counts"] = dict(counts.get(module_dir, {}))
            module["endpoint_count"] = endpoints_per_module.get(module_dir, 0)
            module["client_base_urls"] = self.a.client_base_urls.get(module_dir, [])

            if not module["type_counts"] and not module["config_files"] and info.get("packaging") == "pom":
                module["aggregator"] = True

            out.append(module)

        return out

    # ============================
    # COMPONENTS
    # ============================

    def _components(self) -> List[dict]:
        out = []

        for t in sorted(self.index.types.values(), key=lambda x: x.qualified_name):
            stereotype, layer = self.a.stereotypes.get(t.qualified_name, ("class", "other"))
            if stereotype not in COMPONENT_STEREOTYPES:
                continue

            component = {
                "name": t.name,
                "qualified_name": t.qualified_name,
                "module": t.module,
                "file": t.file,
                "line": t.line_start,
                "kind": t.kind,
                "stereotype": stereotype,
                "layer": layer,
                "annotations": [a.name for a in t.annotations],
                "description": t.javadoc or None,
                "extends": [str(r) for r in t.extends] or None,
                "implements": [str(r) for r in t.implements] or None,
                "dependencies": self._dependencies(t),
                "config_properties": self._config_properties(t),
                "methods": [
                    {"name": m.name, "line": m.line_start, "params": [str(p.type) for p in m.params],
                     "returns": str(m.return_type) if m.return_type else None}
                    for m in t.methods if "private" not in m.modifiers
                ][:100],
                "beans": [
                    {"name": m.name, "type": str(m.return_type)}
                    for m in t.methods if find_annotation(m.annotations, "Bean")
                ] or None,
                "integrations": sorted({
                    f"{f['kind']}:{f.get('technology') or f.get('client')}"
                    for m in t.methods + t.constructors for f in self.a.facts.get(m.id, [])
                    if f.get("kind") not in ("transaction",)
                }) or None,
            }

            if stereotype == "repository" and t.qualified_name in self.a.repositories:
                repo = self.a.repositories[t.qualified_name]
                component["entity"] = repo.get("entity")
                component["store"] = repo.get("store")
            if stereotype == "feign_client" and t.qualified_name in self.a.feign_clients:
                component["feign"] = self.a.feign_clients[t.qualified_name]

            out.append({k: v for k, v in component.items() if v is not None})

        return out

    def _dependencies(self, t: TypeInfo):
        deps = []
        constructor_injection = (
            t.has_annotation(*INJECTING_CONSTRUCTOR_ANNOTATIONS)
            or len(t.constructors) == 1
            or any(find_annotation(c.annotations, "Autowired", "Inject") for c in t.constructors)
        )

        for f in t.fields:
            if f.is_static:
                continue
            injected = {a.name for a in f.annotations} & (INJECTION_ANNOTATIONS - {"Value"})
            if injected:
                deps.append(self._dependency(f.type, f.name, "field", t))
            elif "final" in f.modifiers and t.has_annotation("RequiredArgsConstructor") and f.init is None:
                deps.append(self._dependency(f.type, f.name, "constructor", t))

        if constructor_injection:
            for c in t.constructors:
                for p in c.params:
                    if not any(d["field"] == p.name for d in deps) and not find_annotation(p.annotations, "Value"):
                        deps.append(self._dependency(p.type, p.name, "constructor", t))

        for m in t.methods:
            if find_annotation(m.annotations, "Autowired") and m.name.startswith("set") and m.params:
                deps.append(self._dependency(m.params[0].type, m.params[0].name, "setter", t))

        return dedupe(deps)

    def _dependency(self, type_ref, name, injection, context):
        target = self.index.resolve(type_ref.name, context)
        entry = {"field": name, "type": str(type_ref), "injection": injection}
        if target:
            entry["target"] = target.qualified_name
            entry["target_stereotype"] = self.a.stereotype(target.qualified_name)
            impls = [s.qualified_name for s in self.index.all_subtypes(target) if s.kind == "class"]
            if target.kind == "interface" and impls:
                entry["implementations"] = impls
        else:
            kind = self.a.client_kind(type_ref)
            if kind:
                entry["client"] = kind
        return entry

    def _config_properties(self, t: TypeInfo):
        props = []
        for f in t.fields:
            value = find_annotation(f.annotations, "Value")
            if value and value.arg("value") and value.arg("value")[0] == "str":
                props.append(value.arg("value")[1])
        binding = t.annotation("ConfigurationProperties")
        if binding:
            prefix = self.a.string(binding.arg("prefix", "value"), None, t)
            if prefix:
                props.append(f"{prefix}.*")
        return props or None

    # ============================
    # DATA
    # ============================

    def _tables(self):
        tables = {}

        for entity in self.a.entities.values():
            entry = tables.setdefault(entity["table"], {"name": entity["table"], "store": entity["store"],
                                                        "entities": [], "operations": set(), "accessed_by": set(),
                                                        "modules": set()})
            entry["entities"].append(entity["name"])
            entry["modules"].add(entity["module"])

        for mid, facts in self.a.facts.items():
            for f in facts:
                if f.get("kind") != "database":
                    continue
                for table in f.get("table") or []:
                    entry = tables.setdefault(table, {"name": table, "store": f.get("technology"),
                                                      "entities": [], "operations": set(), "accessed_by": set(),
                                                      "modules": set()})
                    if f.get("operation"):
                        entry["operations"].add(f["operation"])
                    entry["accessed_by"].add(f.get("source"))
                    entry["modules"].add(f.get("module"))

        return [
            {**v, "operations": sorted(v["operations"]), "accessed_by": sorted(filter(None, v["accessed_by"])),
             "modules": sorted(m for m in v["modules"] if m is not None)}
            for _, v in sorted(tables.items())
        ]

    # ============================
    # INTEGRATIONS
    # ============================

    def _all_facts(self, kind):
        return dedupe([
            {k: v for k, v in f.items()}
            for facts in self.a.facts.values() for f in facts if f.get("kind") == kind
        ])

    def _integrations(self):
        producers = self._all_facts("messaging")
        consumers = [dict(l) for l in self.a.listeners if l["technology"] != "spring-event"]

        topics = {}
        for p in producers:
            key = (p.get("technology"), p.get("destination"))
            topics.setdefault(key, {"technology": key[0], "destination": key[1], "producers": [], "consumers": []})
            topics[key]["producers"].append(p.get("source"))
        for c in consumers:
            for dest in c.get("destinations") or [None]:
                key = (c.get("technology"), dest)
                topics.setdefault(key, {"technology": key[0], "destination": dest, "producers": [], "consumers": []})
                topics[key]["consumers"].append(c.get("handler"))

        return {
            "databases": self._all_facts("database"),
            "caches": self._all_facts("cache"),
            "messaging": {
                "producers": producers,
                "consumers": consumers,
                "destinations": sorted(
                    ({**v, "producers": sorted(set(filter(None, v["producers"]))), "consumers": sorted(set(filter(None, v["consumers"])))}
                     for v in topics.values()),
                    key=lambda v: (str(v["technology"]), str(v["destination"])),
                ),
            },
            "external_apis": self._all_facts("http"),
            "feign_clients": sorted(self.a.feign_clients.values(), key=lambda f: f["name"]),
            "events": {
                "published": self._all_facts("event"),
                "listeners": [l for l in self.a.listeners if l["technology"] == "spring-event"],
            },
            "scheduled_jobs": self.a.scheduled,
            "email": self._all_facts("email"),
            "storage": self._all_facts("storage"),
            "search": self._all_facts("search"),
            "resilience": self._all_facts("resilience"),
            "async": self._all_facts("async"),
        }

    def _security(self):
        sec = self.a.security
        endpoint_annotations = sum(1 for e in self.endpoints if (e.get("security") or {}).get("annotations"))
        technologies = sorted({
            d["technology"] for info in self.a.modules.values() for d in info.get("dependencies", [])
            if d.get("category") == "security"
        })
        return {
            "enabled": bool(technologies or sec["mechanisms"] or sec["rules"]),
            "technologies": technologies,
            "mechanisms": sec["mechanisms"],
            "csrf_disabled": sec.get("csrf_disabled", False),
            "url_rules": sec["rules"],
            "configured_in": sec["configured_in"],
            "endpoints_with_method_security": endpoint_annotations,
        }

    # ============================
    # ENDPOINT SUMMARY
    # ============================

    def _endpoint_summary(self, e):
        integrations = e.get("integrations") or {}
        return {
            "method": e["method"],
            "path": e["path"],
            "handler": e["handler"],
            "doc_name": e.get("doc_name"),
            "module": e["module"],
            "summary": e.get("summary") or (e.get("description") or "")[:200] or None,
            "request_body": (e.get("request_body") or {}).get("type"),
            "response": (e.get("response") or {}).get("body_type"),
            "status_codes": sorted({s["code"] for s in e.get("status_codes", [])}),
            "secured": bool(e.get("security")),
            "tables": sorted({t for f in integrations.get("databases", []) for t in (f.get("table") or [])}),
            "caches": sorted({c for f in integrations.get("caches", []) for c in (f.get("cache_names") or [f.get("key") or f.get("technology")]) if c}),
            "publishes": sorted({str(f.get("destination")) for f in integrations.get("messaging", []) if f.get("direction") == "publish"}),
            "calls_external": sorted({f.get("target_service") or f.get("host") or f.get("url") or "?" for f in integrations.get("external_apis", [])}),
        }

    # ============================
    # SUMMARY
    # ============================

    def _summary(self, modules, components):
        stereotypes = defaultdict(int)
        for c in components:
            stereotypes[c["stereotype"]] += 1

        technologies = defaultdict(set)
        for m in modules:
            for d in m.get("dependencies", []):
                if d.get("category") and d["category"] not in ("test", "code-generation"):
                    technologies[d["category"]].add(d["technology"])

        return {
            "modules": len([m for m in modules if not m.get("aggregator")]),
            "spring_boot_apps": [m["runtime"].get("application_name") or m["name"] for m in modules if m["is_spring_boot_app"]],
            "types": len(self.index.types),
            "components": dict(stereotypes),
            "endpoints": len(self.endpoints),
            "entities": len(self.a.entities),
            "repositories": len(self.a.repositories),
            "feign_clients": len(self.a.feign_clients),
            "message_listeners": len([l for l in self.a.listeners if l["technology"] != "spring-event"]),
            "scheduled_jobs": len(self.a.scheduled),
            "technologies": {k: sorted(v) for k, v in sorted(technologies.items())},
            "architecture_style": "microservices" if len([m for m in modules if m["is_spring_boot_app"]]) > 1 else "monolith",
        }

    # ============================
    # GRAPH
    # ============================

    def _runtime(self, module):
        cache = self.__dict__.setdefault("_runtime_cache", {})
        if module not in cache:
            config = self.a.configs.get(module)
            cache[module] = derive_infrastructure(config) if config else {}
        return cache[module]

    def _graph(self, modules, components):
        nodes: Dict[str, dict] = {}
        edges: Dict[tuple, dict] = {}
        module_names = {m["path"]: (m["runtime"].get("application_name") or m["name"]) for m in modules}

        def node(node_id, **attrs):
            if node_id not in nodes:
                nodes[node_id] = {"id": node_id, **attrs}
            return node_id

        def edge(src, dst, kind, detail=None):
            if src == dst:
                return
            key = (src, dst, kind)
            e = edges.setdefault(key, {"from": src, "to": dst, "kind": kind, "details": []})
            if detail and detail not in e["details"] and len(e["details"]) < 50:
                e["details"].append(detail)

        for m in modules:
            if m.get("aggregator"):
                continue
            node(f"module:{m['path']}", type="module", name=module_names[m["path"]], path=m["path"],
                 spring_boot_app=m["is_spring_boot_app"], port=m["runtime"].get("server_port"))

        component_ids = {}
        for c in components:
            cid = node(f"component:{c['qualified_name']}", type="component", name=c["name"],
                       stereotype=c["stereotype"], layer=c["layer"], module=c["module"])
            component_ids[c["qualified_name"]] = cid
            edge(f"module:{c['module']}", cid, "contains")

            for dep in c.get("dependencies", []):
                for target in dep.get("implementations") or [dep.get("target")]:
                    if target in component_ids or target in self.index.types:
                        edge(cid, f"component:{target}", "injects", dep["field"])

        # component -> component calls
        for mid, callees in self.a.calls.items():
            src = self.a.method_owner[mid].qualified_name
            for callee in callees:
                dst = self.a.method_owner.get(callee)
                if dst is not None and src in component_ids and dst.qualified_name in component_ids:
                    edge(component_ids[src], component_ids[dst.qualified_name], "calls", self.a.label(callee))

        # infrastructure from config; remember each module's configured stores
        configured_store = {}
        for m in modules:
            mid = f"module:{m['path']}"
            runtime = m["runtime"]
            for ds in runtime.get("datasources", []):
                nid = node(f"datastore:{ds.get('type')}:{ds.get('host', 'embedded')}/{ds.get('database', '')}",
                           type="datastore", technology=ds.get("type"), host=ds.get("host"), database=ds.get("database"))
                edge(mid, nid, "connects", ds.get("profile"))
                if ds.get("profile") == "default" or len(runtime["datasources"]) == 1:
                    configured_store[(m["path"], "relational")] = nid
            if runtime.get("mongodb"):
                mongo = runtime["mongodb"]
                nid = node(f"datastore:mongodb:{mongo.get('host', 'default')}/{mongo.get('database', '')}",
                           type="datastore", technology="mongodb", host=mongo.get("host"), database=mongo.get("database"))
                edge(mid, nid, "connects")
                configured_store[(m["path"], "mongodb")] = nid
            if runtime.get("redis"):
                nid = node(f"cache:redis:{runtime['redis'].get('host', 'default')}", type="cache", technology="redis",
                           host=runtime["redis"].get("host"))
                edge(mid, nid, "connects")
            for broker in ("kafka", "rabbitmq"):
                if runtime.get(broker):
                    nid = node(f"broker:{broker}", type="broker", technology=broker)
                    edge(mid, nid, "connects")
            if runtime.get("service_discovery"):
                nid = node(f"infra:discovery:{runtime['service_discovery'].get('type')}", type="infrastructure",
                           technology=runtime["service_discovery"].get("type"), url=runtime["service_discovery"].get("url"))
                edge(mid, nid, "registers")
            if runtime.get("config_server"):
                nid = node("infra:config-server", type="infrastructure", technology="spring-cloud-config",
                           url=runtime["config_server"])
                edge(mid, nid, "reads_config")
            if runtime.get("identity_provider"):
                nid = node("infra:identity-provider", type="infrastructure", technology="oauth2",
                           url=runtime["identity_provider"])
                edge(mid, nid, "authenticates")
            for route in runtime.get("gateway_routes", []):
                target = route.get("service_id") or route.get("uri") or route.get("url") or ""
                target_name = target.replace("lb://", "").split("://")[-1].split(":")[0].split("/")[0]
                target_module = self.a.app_names.get(target_name)
                dst = f"module:{target_module}" if target_module is not None else node(
                    f"external:{target_name}", type="external_service", name=target_name)
                edge(mid, dst, "routes", ", ".join(route.get("predicates") or [route.get("path") or ""]))

        # facts -> external systems
        for method_id, facts in self.a.facts.items():
            owner = self.a.method_owner[method_id]
            src = component_ids.get(owner.qualified_name) or f"module:{owner.module}"
            for f in facts:
                kind = f.get("kind")
                if kind == "database":
                    tech = f.get("technology")
                    family = "mongodb" if tech == "mongodb" else "relational" if tech in ("jpa", "jdbc", "r2dbc", "hibernate_session") else tech
                    nid = configured_store.get((owner.module, family)) or node(
                        f"store:{owner.module}:{tech}", type="datastore", technology=tech, module=owner.module,
                        profiles=[f"{d.get('profile')}: {d.get('type')}" for d in self._runtime(owner.module).get("datasources", [])] or None,
                    )
                    for table in f.get("table") or [None]:
                        edge(src, nid, f.get("operation", "uses").lower(), table)
                elif kind == "cache":
                    tech = f.get("technology")
                    nid = node(f"cache:{owner.module}:{tech}", type="cache", technology=tech, module=owner.module)
                    edge(src, nid, f.get("operation", "uses").lower(), ", ".join(f.get("cache_names") or []) or f.get("key"))
                elif kind == "messaging":
                    nid = node(f"broker:{f.get('technology')}", type="broker", technology=f.get("technology"))
                    edge(src, nid, "publishes", f.get("destination"))
                elif kind == "http":
                    if f.get("target_module") is not None:
                        dst = f"module:{f['target_module']}"
                    else:
                        target = f.get("target_service") or f.get("host") or "unresolved"
                        dst = node(f"external:{target}", type="external_service", name=target)
                    edge(src, dst, "http", " ".join(filter(None, [f.get("http_method"), f.get("url")])))
                elif kind in ("email", "storage", "search"):
                    nid = node(f"{kind}:{f.get('technology')}", type=kind, technology=f.get("technology"))
                    edge(src, nid, f.get("operation", "uses").lower() if f.get("operation") else "uses", f.get("bucket"))

        for listener in self.a.listeners:
            if listener["technology"] == "spring-event":
                continue
            owner = self.a.method_owner.get(listener["method_id"])
            src = component_ids.get(owner.qualified_name) if owner else None
            nid = node(f"broker:{listener['technology']}", type="broker", technology=listener["technology"])
            for dest in listener.get("destinations") or [None]:
                edge(nid, src or f"module:{listener['module']}", "delivers", dest)

        for job in self.a.scheduled:
            owner = self.a.method_owner.get(job["method_id"])
            if owner and owner.qualified_name in component_ids:
                nid = node("infra:scheduler", type="infrastructure", technology="spring-scheduling")
                edge(nid, component_ids[owner.qualified_name], "triggers", job["handler"])

        # module -> module edges derived from component edges
        module_edges = {}
        for (src, dst, kind), e in edges.items():
            if kind in ("contains", "injects", "calls"):
                continue
            src_module = nodes.get(src, {}).get("module")
            if src.startswith("component:") and dst.startswith("module:") and src_module is not None:
                key = (f"module:{src_module}", dst, kind)
                module_edges.setdefault(key, {"from": key[0], "to": dst, "kind": kind, "details": []})
                module_edges[key]["details"].extend(d for d in e["details"] if d not in module_edges[key]["details"])

        all_edges = list(edges.values()) + [e for k, e in module_edges.items() if k not in edges]

        return {
            "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
            "edges": sorted(all_edges, key=lambda e: (e["from"], e["to"], e["kind"])),
        }
