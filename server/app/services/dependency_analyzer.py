"""
Dependency Analyzer Service
Analyzes API dependencies, breaking changes impact, and creates dependency graphs
"""
import os
import re
from typing import Dict, List, Set
from app.config import DOCS_DIR


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
