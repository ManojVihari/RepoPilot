"""
Dependency Analyzer Service
Analyzes API dependencies, breaking changes impact, and creates dependency graphs
"""
import re
from typing import Dict, List, Optional, Set
from app.services import architecture_view, docs_store
from mergeclear.contracts import change, contract_changes, dependency_items, downstream, downstream_changes  # noqa: F401 (re-exported)


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


def build_dependency_graph(repo: str) -> Dict:
    """Relationships between the APIs of a repository guessed from their latest docs (no scanner data)."""
    nodes = []
    links = []
    api_docs = {}

    for doc in docs_store.latest_docs(repo):
        api_name = doc["api"]
        api_docs[api_name] = {
            "content": doc["content"][:1000],
            "endpoints": extract_endpoints_from_doc(doc["content"]),
            "version": str(doc["version"]),
        }
        nodes.append({"id": api_name, "label": doc["title"], "version": str(doc["version"]), "type": "api"})

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


def get_impact_analysis(repo: str, api: str, v1_doc: str, v2_doc: str) -> Dict:
    """
    Complete impact analysis including related APIs and breaking changes.
    
    Args:
        repo: Repository name
        api: API name
        v1_doc: Version 1 documentation
        v2_doc: Version 2 documentation
        
    Returns:
        Complete impact analysis
    """
    # Get breaking changes
    breaking_changes = extract_dependencies(v1_doc, v2_doc)
    
    # Build dependency graph
    dependency_graph = build_dependency_graph(repo)
    
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
            change(b.get("type"), "breaking" if b.get("type") in ("FIELD_REMOVED", "TYPE_CHANGED", "VALIDATION_ADDED", "VALIDATION_CHANGED") or b.get("required") else "minor",
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
        "dependencies": dependency_items(v1_route, v2_route) if v2_route else {},
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
