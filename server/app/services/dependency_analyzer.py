"""
Dependency Analyzer Service
Analyzes API dependencies, breaking changes impact, and creates dependency graphs
"""
import os
import re
from typing import Dict, List, Optional, Set
from app.config import DOCS_DIR
from app.services import architecture_view


def extract_endpoints_from_doc(doc_content: str) -> Set[str]:
    """
    Extract endpoint patterns from API documentation.
    
    Args:
        doc_content: Markdown documentation
        
    Returns:
        Set of endpoint patterns found
    """
    endpoints = set()
    
    # Match patterns like /api/v1/users, /endpoint/{id}, etc.
    patterns = [
        r'/[a-zA-Z0-9/_\-{}]+',  # HTTP endpoints
        r'GET|POST|PUT|DELETE|PATCH',  # HTTP methods
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, doc_content)
        endpoints.update(matches)
    
    return endpoints


def extract_dependencies(doc_v1: str, doc_v2: str) -> Dict:
    """
    Identify potential dependencies and related APIs based on documentation.
    
    Args:
        doc_v1: Previous version documentation
        doc_v2: Current version documentation
        
    Returns:
        Dictionary with breaking changes and impact analysis
    """
    endpoints_v1 = extract_endpoints_from_doc(doc_v1)
    endpoints_v2 = extract_endpoints_from_doc(doc_v2)
    
    # Identify changes
    added = endpoints_v2 - endpoints_v1
    removed = endpoints_v1 - endpoints_v2
    
    # Breaking changes analysis
    breaking_changes = {
        "removed_endpoints": list(removed),
        "added_endpoints": list(added),
        "potentially_affected": _find_potentially_affected(removed),
        "migration_required": len(removed) > 0,
        "impact_level": _calculate_impact_level(removed, endpoints_v1),
    }
    
    return breaking_changes


def _find_potentially_affected(removed_endpoints: List[str]) -> List[Dict]:
    """Find APIs that might be affected by removed endpoints."""
    affected = []
    
    for endpoint in removed_endpoints:
        # Extract common patterns (e.g., /users, /orders)
        parts = endpoint.split('/')
        resource = parts[-1] if parts else ""
        
        if resource:
            affected.append({
                "endpoint": endpoint,
                "resource": resource,
                "dependent_apis": [
                    f"Any API using {resource}",
                    f"Any service consuming {endpoint}",
                ],
                "migration_suggestion": f"Clients must migrate from {endpoint} to new endpoint"
            })
    
    return affected


def _calculate_impact_level(removed: Set[str], total: Set[str]) -> str:
    """Calculate breaking change impact level."""
    if not total:
        return "none"
    
    impact_ratio = len(removed) / len(total)
    
    if impact_ratio == 0:
        return "none"
    elif impact_ratio < 0.2:
        return "low"
    elif impact_ratio < 0.5:
        return "medium"
    else:
        return "high"


def build_dependency_graph(repo: str, base_path: str = DOCS_DIR) -> Dict:
    """
    Build a dependency graph of all APIs in a repository.
    
    Args:
        repo: Repository name
        base_path: Base path to docs
        
    Returns:
        Graph structure for visualization
    """
    repo_path = os.path.join(base_path, repo)
    nodes = []
    links = []
    
    if not os.path.exists(repo_path):
        return {"nodes": [], "links": []}
    
    # Collect all APIs
    api_docs = {}
    for api_name in os.listdir(repo_path):
        if api_name.startswith("."):
            continue
        
        api_path = os.path.join(repo_path, api_name)
        if not os.path.isdir(api_path):
            continue
        
        # Get latest version
        versions = [
            f for f in os.listdir(api_path)
            if f.startswith("v") and f.endswith(".md")
        ]
        
        if versions:
            latest = max(versions, key=lambda x: int(x.replace("v", "").replace(".md", "")))
            doc_path = os.path.join(api_path, latest)
            
            try:
                with open(doc_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    api_docs[api_name] = {
                        "content": content[:1000],  # Preview
                        "endpoints": extract_endpoints_from_doc(content),
                        "version": latest.replace("v", "").replace(".md", "")
                    }
                    
                    # Add node
                    nodes.append({
                        "id": api_name,
                        "label": api_name,
                        "version": api_docs[api_name]["version"],
                        "type": "api"
                    })
            except Exception as e:
                print(f"⚠️  Error reading {api_name}: {e}")
                continue
    
    # Find connections based on shared endpoints
    api_names = list(api_docs.keys())
    for i, api1 in enumerate(api_names):
        for api2 in api_names[i+1:]:
            endpoints1 = api_docs[api1]["endpoints"]
            endpoints2 = api_docs[api2]["endpoints"]
            
            # Find common endpoints or resources
            common = endpoints1 & endpoints2
            
            if common or _has_semantic_relationship(api1, api2):
                links.append({
                    "source": api1,
                    "target": api2,
                    "type": "related",
                    "shared_resources": list(common)[:3]  # Top 3
                })
    
    return {
        "nodes": nodes,
        "links": links,
        "repo": repo,
        "total_apis": len(nodes)
    }


def _has_semantic_relationship(api1: str, api2: str) -> bool:
    """Check if two API names have semantic relationship."""
    # Common related patterns
    relationships = {
        "user": ["profile", "auth", "account", "permission"],
        "order": ["payment", "shipping", "inventory", "product"],
        "product": ["inventory", "catalog", "pricing", "category"],
        "auth": ["user", "permission", "token", "session"],
    }
    
    api1_lower = api1.lower()
    api2_lower = api2.lower()
    
    for key, related in relationships.items():
        if key in api1_lower:
            for rel in related:
                if rel in api2_lower:
                    return True
        if key in api2_lower:
            for rel in related:
                if rel in api1_lower:
                    return True
    
    return False


def get_impact_analysis(repo: str, api: str, v1_doc: str, v2_doc: str, base_path: str = DOCS_DIR) -> Dict:
    """
    Complete impact analysis including related APIs and breaking changes.
    
    Args:
        repo: Repository name
        api: API name
        v1_doc: Version 1 documentation
        v2_doc: Version 2 documentation
        base_path: Base path to docs
        
    Returns:
        Complete impact analysis
    """
    # Get breaking changes
    breaking_changes = extract_dependencies(v1_doc, v2_doc)
    
    # Build dependency graph
    dependency_graph = build_dependency_graph(repo, base_path)
    
    # Find APIs that might be affected
    affected_apis = []
    current_api_node = None
    
    for node in dependency_graph["nodes"]:
        if node["id"] == api:
            current_api_node = node
            break
    
    if current_api_node:
        # Find all connected APIs
        for link in dependency_graph["links"]:
            if link["source"] == api:
                affected_apis.append(link["target"])
            elif link["target"] == api:
                affected_apis.append(link["source"])
    
    return {
        "api": api,
        "repo": repo,
        "breaking_changes": breaking_changes,
        "affected_apis": affected_apis,
        "dependency_graph": dependency_graph,
        "recommendations": _generate_recommendations(breaking_changes, affected_apis)
    }


def _generate_recommendations(breaking_changes: Dict, affected_apis: List[str]) -> List[str]:
    """Generate recommendations based on impact analysis."""
    recommendations = []
    
    impact_level = breaking_changes.get("impact_level", "none")
    
    if impact_level == "high":
        recommendations.append("🚨 HIGH IMPACT: Schedule migration meetings with dependent teams")
        recommendations.append("📋 Create detailed migration guide for clients")
        recommendations.append("⏱️  Plan extended deprecation period (90+ days)")
    
    elif impact_level == "medium":
        recommendations.append("⚠️  MEDIUM IMPACT: Notify affected teams about changes")
        recommendations.append("📝 Update documentation with migration path")
        recommendations.append("🔔 Send deprecation notices to API consumers")
    
    elif impact_level == "low":
        recommendations.append("ℹ️  LOW IMPACT: Changes are minor")
        recommendations.append("✅ Update changelog and release notes")
    
    if affected_apis:
        recommendations.append(f"🔗 {len(affected_apis)} related APIs may be affected: {', '.join(affected_apis[:3])}")
    
    if breaking_changes.get("removed_endpoints"):
        recommendations.append("🛠️  Plan backward compatibility layer or version strategy")
    
    return recommendations


# ============================================================================
# SCANNER-BASED IMPACT ANALYSIS (structured route data + application model)
# ============================================================================

def _flatten_schema(schema, prefix="") -> Dict[str, dict]:
    """{field: {type, validation}} incl. nested object/array fields as dotted paths."""
    out = {}
    if not isinstance(schema, dict):
        return out

    for name, details in schema.items():
        if not isinstance(details, dict):
            continue
        path = f"{prefix}{name}"
        out[path] = {"type": details.get("type"), "validation": details.get("validation") or {}}

        nested = details.get("schema") or {}
        if nested.get("type") == "array":
            nested = nested.get("items") or {}
        if nested.get("fields"):
            out.update(_flatten_schema(nested["fields"], f"{path}."))

    return out


def _response_fields(route) -> Dict[str, dict]:
    schema = (route.get("response") or {}).get("schema")
    if isinstance(schema, dict) and schema.get("type") == "array":
        schema = (schema.get("items") or {}).get("fields") or {}
    return _flatten_schema(schema if isinstance(schema, dict) else {})


def _change(kind, severity, detail, **extra):
    return {"type": kind, "severity": severity, "detail": detail, **extra}


def contract_changes(old: dict, new: dict) -> List[dict]:
    """Contract differences between two versions of an endpoint."""
    changes = []

    if (old.get("method"), old.get("path")) != (new.get("method"), new.get("path")):
        changes.append(_change("ENDPOINT_CHANGED", "breaking",
                               f"{old.get('method')} {old.get('path')} → {new.get('method')} {new.get('path')}"))

    # ---- parameters (path / query / header / cookie) ----
    def params(route):
        return {(p.get("in"), p.get("name")): p for p in route.get("params") or [] if p.get("in") not in ("body", "model")}

    old_params, new_params = params(old), params(new)
    for key, p in new_params.items():
        location, name = key
        if key not in old_params:
            severity = "breaking" if p.get("required") else "additive"
            changes.append(_change("PARAM_ADDED", severity, f"{location} parameter `{name}` ({p.get('type')})"
                                   + (" is required" if p.get("required") else " (optional)")))
            continue
        before = old_params[key]
        if before.get("type") != p.get("type"):
            changes.append(_change("PARAM_TYPE_CHANGED", "breaking", f"{location} parameter `{name}`: {before.get('type')} → {p.get('type')}"))
        if p.get("required") and not before.get("required"):
            changes.append(_change("PARAM_NOW_REQUIRED", "breaking", f"{location} parameter `{name}` became required"))
    for key, p in old_params.items():
        if key not in new_params:
            location, name = key
            changes.append(_change("PARAM_REMOVED", "breaking" if location == "path" else "minor",
                                   f"{location} parameter `{name}` removed"))

    # ---- request body ----
    old_body, new_body = old.get("request_body") or {}, new.get("request_body") or {}
    if old_body.get("type") != new_body.get("type") and (old_body or new_body):
        changes.append(_change("REQUEST_BODY_CHANGED", "breaking", f"request body {old_body.get('type')} → {new_body.get('type')}"))

    old_fields, new_fields = _flatten_schema(old_body.get("schema")), _flatten_schema(new_body.get("schema"))
    for field, details in new_fields.items():
        before = old_fields.get(field)
        if before is None:
            required = bool(details["validation"].get("required"))
            changes.append(_change("REQUEST_FIELD_ADDED", "breaking" if required else "additive",
                                   f"`{field}` ({details['type']})" + (" is required" if required else " (optional)")))
            continue
        if before["type"] != details["type"]:
            changes.append(_change("REQUEST_FIELD_TYPE_CHANGED", "breaking", f"`{field}`: {before['type']} → {details['type']}"))
        for rule, value in details["validation"].items():
            if rule not in before["validation"]:
                changes.append(_change("VALIDATION_ADDED", "breaking", f"`{field}` now validated: {rule}={value}"))
            elif before["validation"][rule] != value:
                changes.append(_change("VALIDATION_CHANGED", "breaking", f"`{field}` {rule}: {before['validation'][rule]} → {value}"))
        for rule in before["validation"]:
            if rule not in details["validation"]:
                changes.append(_change("VALIDATION_REMOVED", "minor", f"`{field}` no longer validated: {rule}"))
    for field in old_fields:
        if field not in new_fields:
            changes.append(_change("REQUEST_FIELD_REMOVED", "minor", f"`{field}` is no longer read"))

    # ---- response ----
    old_resp, new_resp = old.get("response") or {}, new.get("response") or {}
    if old_resp.get("body_type") != new_resp.get("body_type") and (old_resp.get("body_type") or new_resp.get("body_type")):
        changes.append(_change("RESPONSE_TYPE_CHANGED", "breaking", f"{old_resp.get('body_type')} → {new_resp.get('body_type')}"))

    old_out, new_out = _response_fields(old), _response_fields(new)
    for field, details in old_out.items():
        if field not in new_out:
            changes.append(_change("RESPONSE_FIELD_REMOVED", "breaking", f"`{field}` is no longer returned"))
        elif new_out[field]["type"] != details["type"]:
            changes.append(_change("RESPONSE_FIELD_TYPE_CHANGED", "breaking", f"`{field}`: {details['type']} → {new_out[field]['type']}"))
    for field, details in new_out.items():
        if field not in old_out:
            changes.append(_change("RESPONSE_FIELD_ADDED", "additive", f"`{field}` ({details['type']})"))

    # ---- status codes ----
    old_codes = {s.get("code") for s in old.get("status_codes") or []}
    new_codes = {s.get("code") for s in new.get("status_codes") or []}
    old_success = {c for c in old_codes if c and c < 300}
    new_success = {c for c in new_codes if c and c < 300}
    if old_success and new_success and old_success != new_success:
        changes.append(_change("SUCCESS_STATUS_CHANGED", "breaking", f"{sorted(old_success)} → {sorted(new_success)}"))
    for code in sorted(c for c in new_codes - old_codes if c and c >= 400):
        changes.append(_change("ERROR_STATUS_ADDED", "minor", f"may now return {code}"))
    for code in sorted(c for c in old_codes - new_codes if c and c >= 400):
        changes.append(_change("ERROR_STATUS_REMOVED", "additive", f"no longer returns {code}"))

    # ---- security ----
    def security(route):
        sec = route.get("security") or {}
        annotations = sorted(f"{a.get('annotation')}:{a.get('expression')}" for a in sec.get("annotations") or [])
        rule = sec.get("url_rule") or {}
        return annotations, (rule.get("access"), tuple(rule.get("roles") or []))

    if security(old) != security(new):
        changes.append(_change("SECURITY_CHANGED", "breaking", "access rules changed",
                               old=security(old)[0] or None, new=security(new)[0] or None))

    rank = {"breaking": 0, "minor": 1, "additive": 2}
    return sorted(changes, key=lambda c: rank.get(c["severity"], 3))


def downstream(route: dict) -> Dict[str, List[str]]:
    """Compact view of what an endpoint reaches: tables, caches, topics, external calls."""
    integrations = (route or {}).get("integrations") or {}

    tables = sorted({
        f"{f.get('operation')} {t} ({f.get('technology')})"
        for f in integrations.get("databases", []) for t in (f.get("table") or [f.get("entity") or "?"])
    })
    caches = sorted({
        f"{f.get('operation')} {', '.join(f.get('cache_names') or []) or f.get('key') or f.get('technology')} ({f.get('technology')})"
        for f in integrations.get("caches", [])
    })
    messaging = sorted({
        f"{f.get('direction')} {f.get('destination')} ({f.get('technology')})" for f in integrations.get("messaging", [])
    })
    external = sorted({
        f"{f.get('http_method') or ''} {f.get('url') or ''}".strip() + (f" → {f.get('target_service') or f.get('host')}" if (f.get("target_service") or f.get("host")) else "")
        for f in integrations.get("external_apis", [])
    })
    other = sorted({
        f"{group}: {f.get('event_type') or f.get('bucket') or f.get('technology')}"
        for group in ("events", "storage", "email", "search") for f in integrations.get(group, [])
    })

    return {"databases": tables, "caches": caches, "messaging": messaging, "external_apis": external, "other": other}


def downstream_changes(old: Optional[dict], new: dict) -> Dict[str, Dict[str, List[str]]]:
    if not old:
        return {}
    before, after = downstream(old), downstream(new)
    out = {}
    for group in after:
        added = sorted(set(after[group]) - set(before.get(group, [])))
        removed = sorted(set(before.get(group, [])) - set(after[group]))
        if added or removed:
            out[group] = {"added": added, "removed": removed}
    return out


def _impact_level(changes, consumer_count, shared_count, downstream_delta):
    breaking = [c for c in changes if c["severity"] == "breaking"]
    if breaking:
        return "high" if (consumer_count or shared_count) else "medium"
    if changes or downstream_delta:
        return "low"
    return "none"


def _impact_graph(endpoint, route, consumer_list, shared, model):
    nodes, links = {}, []
    center = f"endpoint:{endpoint['method']} {endpoint['path']}"
    nodes[center] = {"id": center, "label": f"{endpoint['method']} {endpoint['path']}", "type": "endpoint"}

    def add(node_id, label, node_type, kind, detail="", reverse=False):
        nodes.setdefault(node_id, {"id": node_id, "label": label, "type": node_type})
        link = {"source": node_id, "target": center} if reverse else {"source": center, "target": node_id}
        links.append({**link, "kind": kind, "details": [detail] if detail else []})

    for c in consumer_list:
        add(f"caller:{c['caller']}", c["caller"], c["kind"], "calls this API" if c["kind"] == "service_call" else "routes to", c["detail"], reverse=True)

    integrations = (route or {}).get("integrations") or {}
    for f in integrations.get("databases", []):
        for table in f.get("table") or [f.get("entity") or "?"]:
            add(f"table:{table}", table, "table", (f.get("operation") or "uses").lower(), f.get("call") or "")
    for f in integrations.get("caches", []):
        name = ", ".join(f.get("cache_names") or []) or f.get("key") or f.get("technology")
        add(f"cache:{name}", name, "cache", (f.get("operation") or "uses").lower(), f.get("technology"))
    for f in integrations.get("messaging", []):
        add(f"topic:{f.get('destination')}", str(f.get("destination")), "topic", f.get("direction") or "publish", f.get("technology"))
    for f in integrations.get("external_apis", []):
        target = f.get("target_service") or f.get("host") or "unresolved"
        add(f"service:{target}", target, "external_service", "http", f"{f.get('http_method') or ''} {f.get('url') or ''}".strip())

    if not route:
        for table in endpoint.get("tables") or []:
            add(f"table:{table}", table, "table", "uses")
        for topic in endpoint.get("publishes") or []:
            add(f"topic:{topic}", topic, "topic", "publish")
        for target in endpoint.get("calls_external") or []:
            add(f"service:{target}", target, "external_service", "http")

    for s in shared:
        resource = f"{s['kind'] if s['kind'] != 'table' else 'table'}:{s['name']}"
        if s["kind"] == "topic":
            resource = f"topic:{s['name']}"
        nodes.setdefault(resource, {"id": resource, "label": s["name"], "type": s["kind"]})
        for other in s["also_used_by"][:8]:
            other_id = f"api:{other}"
            nodes.setdefault(other_id, {"id": other_id, "label": other.split(" (")[0], "type": "related_endpoint"})
            links.append({"source": other_id, "target": resource, "kind": "shares", "details": []})

    return {"nodes": list(nodes.values()), "links": links}


def _recommendations(changes, consumer_list, shared, delta):
    recs = []
    breaking = [c for c in changes if c["severity"] == "breaking"]

    if breaking:
        recs.append(f"{len(breaking)} breaking change(s): version the endpoint or keep the old contract until clients migrate")
    if breaking and consumer_list:
        callers = sorted({c["caller_module"] or c["caller"] for c in consumer_list})
        recs.append(f"Coordinate the release with calling services: {', '.join(callers)}")
    if any(c["type"] in ("VALIDATION_ADDED", "VALIDATION_CHANGED", "PARAM_NOW_REQUIRED", "REQUEST_FIELD_ADDED") and c["severity"] == "breaking" for c in changes):
        recs.append("Stricter input rules: requests that passed before can now fail with 400 - check client payloads")
    if any(c["type"].startswith("RESPONSE_") and c["severity"] == "breaking" for c in changes):
        recs.append("Response shape changed: update client deserialization and contract tests")
    if any(c["type"] == "SECURITY_CHANGED" for c in changes):
        recs.append("Access rules changed: verify roles/scopes of every client")
    for group, diff in delta.items():
        if diff.get("added"):
            recs.append(f"New downstream {group.replace('_', ' ')}: {', '.join(diff['added'][:3])} - check availability, permissions and load")
    shared_tables = [s for s in shared if s["kind"] == "table"]
    if shared_tables and (breaking or delta.get("databases")):
        recs.append(f"Shares data with {sum(len(s['also_used_by']) for s in shared_tables)} other endpoint(s) through {', '.join(s['name'] for s in shared_tables[:3])}: regression-test them")
    if not recs:
        recs.append("No contract change detected between these versions")
    return recs


def analyze_endpoint_impact(repo: str, api: str, v1: Optional[int], v2: int,
                            v1_route: Optional[dict], v2_route: Optional[dict],
                            architecture: Optional[dict]) -> Optional[Dict]:
    """Impact analysis from scanner data; None when there is nothing structured to use."""
    model = architecture_view.spring_model(architecture)
    endpoint = architecture_view.find_endpoint(model, api, v2_route) if model else None

    if v2_route is None and endpoint is None:
        return None

    if endpoint is None:
        endpoint = {"method": v2_route.get("method"), "path": v2_route.get("path"),
                    "handler": v2_route.get("handler") or api, "module": v2_route.get("module", "")}

    if v1_route and v2_route:
        changes = contract_changes(v1_route, v2_route)
    else:
        changes = [
            _change(b.get("type"), "breaking" if b.get("type") in ("FIELD_REMOVED", "TYPE_CHANGED", "VALIDATION_ADDED", "VALIDATION_CHANGED") or b.get("required") else "minor",
                    f"`{b.get('dto', '')}.{b.get('field')}` {b.get('rule') or b.get('new_type') or ''}".strip())
            for b in (v2_route or {}).get("breaking_changes") or []
        ]

    consumer_list = architecture_view.consumers(model, endpoint) if model else []
    shared = architecture_view.shared_resources(model, endpoint) if model else []
    delta = downstream_changes(v1_route, v2_route) if v2_route else {}

    endpoint_changed = [c for c in changes if c["type"] == "ENDPOINT_CHANGED"]
    affected = sorted({c["caller_module"] or c["caller"] for c in consumer_list}
                      | {u for s in shared for u in s["also_used_by"]})

    return {
        "source": "scanner",
        "api": api,
        "repo": repo,
        "v1": v1,
        "v2": v2,
        "endpoint": endpoint,
        "has_route_data": {"v1": v1_route is not None, "v2": v2_route is not None},
        "has_architecture": model is not None,
        "change_reasons": (v2_route or {}).get("change_reasons") or [],
        "breaking_changes": {
            "changes": changes,
            "breaking_count": len([c for c in changes if c["severity"] == "breaking"]),
            "removed_endpoints": [f"{v1_route.get('method')} {v1_route.get('path')}"] if endpoint_changed and v1_route else [],
            "added_endpoints": [f"{v2_route.get('method')} {v2_route.get('path')}"] if endpoint_changed and v2_route else [],
            "migration_required": any(c["severity"] == "breaking" for c in changes),
            "impact_level": _impact_level(changes, len(consumer_list), len(shared), delta),
        },
        "consumers": consumer_list,
        "shared_resources": shared,
        "downstream": downstream(v2_route) if v2_route else {
            "databases": endpoint.get("tables") or [], "caches": endpoint.get("caches") or [],
            "messaging": endpoint.get("publishes") or [], "external_apis": endpoint.get("calls_external") or [], "other": [],
        },
        "downstream_changes": delta,
        "affected_apis": affected,
        "dependency_graph": _impact_graph(endpoint, v2_route, consumer_list, shared, model),
        "recommendations": _recommendations(changes, consumer_list, shared, delta),
    }


def build_architecture_graph(architecture: Optional[dict], view: str = "system", module: Optional[str] = None) -> Optional[Dict]:
    model = architecture_view.spring_model(architecture)
    if model is None:
        return None
    if view == "components":
        return architecture_view.component_view(model, module)
    return architecture_view.system_view(model)
