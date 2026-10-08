"""
Views over the application model stored by the scanner (architecture_store):
graphs for the UI and the relationships of a single endpoint.
"""
import re
from typing import Dict, List, Optional

STRUCTURAL_EDGES = {"contains", "injects", "calls"}


def spring_model(document: Optional[dict]) -> Optional[dict]:
    """The Spring application model inside a stored architecture document."""
    if not document:
        return None
    return (document.get("architecture") or {}).get("spring")


# ============================
# GRAPHS
# ============================

def _merge_edge(edges: Dict[tuple, dict], src, dst, kind, details):
    if src == dst:
        return
    key = (src, dst, kind)
    edge = edges.setdefault(key, {"source": src, "target": dst, "kind": kind, "details": []})
    for d in details or []:
        if d and d not in edge["details"] and len(edge["details"]) < 30:
            edge["details"].append(d)


def system_view(model: dict) -> dict:
    """Modules and external systems; component edges are lifted to their module."""
    graph = model.get("graph") or {}
    nodes = {n["id"]: n for n in graph.get("nodes", [])}

    def owner(node_id):
        node = nodes.get(node_id, {})
        if node.get("type") == "component":
            return f"module:{node.get('module', '')}"
        return node_id

    edges: Dict[tuple, dict] = {}
    for e in graph.get("edges", []):
        if e["kind"] in STRUCTURAL_EDGES:
            continue
        _merge_edge(edges, owner(e["from"]), owner(e["to"]), e["kind"], e.get("details"))

    used = {e["source"] for e in edges.values()} | {e["target"] for e in edges.values()}
    view_nodes = [
        _view_node(n) for n in nodes.values()
        if n.get("type") != "component" and (n["id"] in used or n.get("type") == "module")
    ]
    return {"nodes": view_nodes, "links": list(edges.values())}


def component_view(model: dict, module: Optional[str] = None) -> dict:
    """Components (optionally of one module) with the systems they touch."""
    graph = model.get("graph") or {}
    nodes = {n["id"]: n for n in graph.get("nodes", [])}

    def in_scope(node_id):
        node = nodes.get(node_id, {})
        return node.get("type") == "component" and (module is None or node.get("module") == module)

    edges: Dict[tuple, dict] = {}
    for e in graph.get("edges", []):
        if e["kind"] == "contains":
            continue
        if in_scope(e["from"]) or in_scope(e["to"]):
            # keep component <-> component edges only inside the scope
            if e["kind"] in ("injects", "calls") and not (in_scope(e["from"]) and in_scope(e["to"])):
                continue
            _merge_edge(edges, e["from"], e["to"], e["kind"], e.get("details"))

    used = {e["source"] for e in edges.values()} | {e["target"] for e in edges.values()}
    used |= {n for n in nodes if in_scope(n)}
    return {
        "nodes": [_view_node(nodes[n]) for n in sorted(used) if n in nodes],
        "links": list(edges.values()),
    }


def _view_node(node: dict) -> dict:
    label = node.get("name") or node.get("technology") or node["id"].split(":", 1)[-1]
    if node.get("type") == "datastore" and node.get("host"):
        label = f"{node.get('technology')} @ {node['host']}"
    elif node.get("type") in ("cache", "broker", "storage", "email", "search") and node.get("technology"):
        label = node["technology"] + (f" @ {node['host']}" if node.get("host") else "")
    return {**node, "label": label}


# ============================
# ENDPOINT RELATIONSHIPS
# ============================

def find_endpoint(model: dict, api: str, route: Optional[dict] = None) -> Optional[dict]:
    """Endpoint summary of an API (docs are keyed by handler method name)."""
    candidates = [e for e in model.get("endpoints", []) if e["handler"].rsplit(".", 1)[-1] == api]

    if route:
        exact = [e for e in candidates if e.get("handler") == route.get("handler")]
        exact = exact or [e for e in candidates if (e["method"], e["path"]) == (route.get("method"), route.get("path"))]
        candidates = exact or candidates

    return candidates[0] if candidates else None


def _module_name(model, module_path):
    for m in model.get("modules", []):
        if m.get("path") == module_path:
            return (m.get("runtime") or {}).get("application_name") or m.get("name") or module_path
    return module_path


def _context_path(model, module_path):
    for m in model.get("modules", []):
        if m.get("path") == module_path:
            return (m.get("runtime") or {}).get("context_path") or ""
    return ""


def _path_regex(path):
    """/owners/{id}/pets -> regex matching concrete or templated variants."""
    parts = []
    for segment in path.strip("/").split("/"):
        if re.fullmatch(r"\{[^}]+\}", segment):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^/" + "/".join(parts) + "/?$")


def _url_path(detail: str) -> Optional[str]:
    """'GET http://svc/owners/{id}?x=1' -> '/owners/{id}'"""
    url = detail.split(" ", 1)[-1] if " " in detail else detail
    url = re.sub(r"^\w+://[^/]+", "", url)
    url = url.split("?", 1)[0]
    return url if url.startswith("/") else None


def _gateway_matches(predicate: str, path: str) -> bool:
    pattern = predicate.split("=", 1)[-1] if "=" in predicate else predicate
    regex = re.escape(pattern.strip()).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.match(regex, path) is not None


def consumers(model: dict, endpoint: dict) -> List[dict]:
    """Who calls this endpoint: other services (HTTP/Feign) and gateways."""
    graph = model.get("graph") or {}
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    module_id = f"module:{endpoint['module']}"
    context = _context_path(model, endpoint["module"])
    regexes = [_path_regex(context + endpoint["path"]), _path_regex(endpoint["path"])]
    found = []

    for e in graph.get("edges", []):
        if e["to"] != module_id:
            continue

        source = nodes.get(e["from"], {})
        caller = source.get("name") or e["from"]
        caller_module = source.get("module") if source.get("type") == "component" else source.get("path")

        if e["kind"] == "http":
            for detail in e.get("details") or []:
                method = detail.split(" ", 1)[0] if " " in detail else None
                path = _url_path(detail)
                if path and any(r.match(path) for r in regexes) and method in (None, endpoint["method"], "ANY"):
                    found.append({
                        "kind": "service_call",
                        "caller": caller,
                        "caller_type": source.get("type"),
                        "caller_module": _module_name(model, caller_module) if caller_module is not None else None,
                        "detail": detail,
                    })

        elif e["kind"] == "routes":
            full_path = context + endpoint["path"]
            for detail in e.get("details") or []:
                if any(_gateway_matches(p.strip(), full_path) for p in detail.split(",")):
                    found.append({
                        "kind": "gateway_route",
                        "caller": caller,
                        "caller_type": source.get("type"),
                        "caller_module": source.get("name"),
                        "detail": detail,
                    })

    # the scanner also lifts component calls to module -> module edges; keep the precise caller
    precise = {(f["caller_module"], f["detail"]) for f in found if f["caller_type"] == "component"}
    unique = []
    for f in found:
        if f["caller_type"] == "module" and f["kind"] == "service_call" and (f["caller_module"], f["detail"]) in precise:
            continue
        if f not in unique:
            unique.append(f)
    return unique


def shared_resources(model: dict, endpoint: dict) -> List[dict]:
    """Tables, caches and destinations this endpoint shares with other endpoints and listeners."""
    others = [e for e in model.get("endpoints", []) if e["handler"] != endpoint["handler"] or e["path"] != endpoint["path"]]
    shared = []

    for kind, key in (("table", "tables"), ("cache", "caches")):
        for name in endpoint.get(key) or []:
            users = sorted({f"{e['method']} {e['path']} ({e['handler']})" for e in others if name in (e.get(key) or [])})
            if users:
                shared.append({"kind": kind, "name": name, "also_used_by": users})

    destinations = (model.get("integrations") or {}).get("messaging", {}).get("destinations", [])
    for name in endpoint.get("publishes") or []:
        for d in destinations:
            if str(d.get("destination")) == name and d.get("consumers"):
                shared.append({"kind": "topic", "name": name, "also_used_by": d["consumers"],
                               "technology": d.get("technology")})

    return shared
