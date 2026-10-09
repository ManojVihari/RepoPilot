"""
Custom Test Templates Service
Allows organizations to define reusable test case patterns
"""
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from app import db
from app.db import test_templates
from app.services.docs_store import is_safe_name


def _template_key(template_name: str) -> str:
    return template_name.strip().lower().replace(" ", "_").replace("-", "_")


def _valid(repo: str, template_name: str) -> bool:
    key = _template_key(template_name or "")
    return is_safe_name(repo) and is_safe_name(key) and len(key) <= 200


def _as_dict(row) -> Dict:
    return {"name": row.name, "category": row.category, "description": row.description,
            "test_cases": row.test_cases, "created_at": row.created_at.isoformat() if row.created_at else None,
            "test_count": len(row.test_cases or [])}


def create_template(repo: str, name: str, category: str, description: str, test_cases: List[str]) -> bool:
    """Create (or replace) a custom test template of a repository."""
    if not _valid(repo, name):
        return False
    with db.engine().begin() as conn:
        db.upsert(conn, test_templates, {
            "repo": repo, "key": _template_key(name), "name": name, "category": category,
            "description": description, "test_cases": list(test_cases or []), "created_at": db.utcnow(),
        }, ("repo", "key"))
    return True


def get_template(repo: str, template_name: str) -> Optional[Dict]:
    if not _valid(repo, template_name):
        return None
    with db.engine().connect() as conn:
        row = conn.execute(select(test_templates).where(
            test_templates.c.repo == repo, test_templates.c.key == _template_key(template_name))).first()
    return _as_dict(row) if row else None


def list_templates(repo: str, category: Optional[str] = None) -> List[Dict]:
    """Templates of a repository, newest first, optionally of one category."""
    query = select(test_templates).where(test_templates.c.repo == repo)
    if category is not None:
        query = query.where(test_templates.c.category == category)
    with db.engine().connect() as conn:
        return [_as_dict(r) for r in conn.execute(query.order_by(test_templates.c.created_at.desc()))]


def delete_template(repo: str, template_name: str) -> bool:
    if not _valid(repo, template_name):
        return False
    with db.engine().begin() as conn:
        return conn.execute(delete(test_templates).where(
            test_templates.c.repo == repo, test_templates.c.key == _template_key(template_name))).rowcount > 0


def get_predefined_templates() -> Dict[str, List[Dict]]:
    """
    Get predefined test templates for common use cases.
    Organizations can use these as starting points.
    
    Returns:
        Dictionary of category -> templates
    """
    return {
        "happy_path": [
            {
                "name": "Happy Path - REST Standard",
                "category": "happy_path",
                "description": "Standard happy path tests for REST APIs",
                "test_cases": [
                    "Create valid resource with all required fields",
                    "Verify successful response with correct status code (200/201)",
                    "Validate response contains all expected fields",
                    "Confirm resource is persisted correctly",
                    "Update existing resource successfully",
                    "Delete resource and verify removal"
                ]
            },
            {
                "name": "Happy Path - Data Flow",
                "category": "happy_path",
                "description": "End-to-end data flow validation",
                "test_cases": [
                    "Input valid data through entire pipeline",
                    "Verify data transformation at each step",
                    "Confirm output matches expected format",
                    "Validate all side effects are applied"
                ]
            }
        ],
        "error_cases": [
            {
                "name": "HTTP Error Codes",
                "category": "error_cases",
                "description": "Standard HTTP error handling",
                "test_cases": [
                    "Missing required field → 400 Bad Request",
                    "Invalid data type → 400 Bad Request",
                    "Missing authentication → 401 Unauthorized",
                    "Insufficient permissions → 403 Forbidden",
                    "Resource not found → 404 Not Found",
                    "Duplicate entry → 409 Conflict",
                    "Rate limit exceeded → 429 Too Many Requests",
                    "Server error → 500 Internal Server Error"
                ]
            },
            {
                "name": "Business Logic Errors",
                "category": "error_cases",
                "description": "Business-specific error scenarios",
                "test_cases": [
                    "Invalid state transition attempt",
                    "Business rule violation",
                    "Insufficient inventory/resources",
                    "Transaction failure handling"
                ]
            }
        ],
        "security_tests": [
            {
                "name": "OWASP Top 10",
                "category": "security_tests",
                "description": "OWASP Top 10 security test cases",
                "test_cases": [
                    "SQL injection in string parameters",
                    "XSS attack in text/HTML fields",
                    "Path traversal in file operations",
                    "CSRF token validation",
                    "Broken authentication check",
                    "Sensitive data exposure",
                    "XML External Entity (XXE) injection",
                    "Broken access control"
                ]
            },
            {
                "name": "Authentication & Authorization",
                "category": "security_tests",
                "description": "Auth-specific security tests",
                "test_cases": [
                    "Endpoint accessible without token",
                    "Using expired token",
                    "Using token from different user",
                    "Privilege escalation attempt",
                    "Token refresh flow security"
                ]
            }
        ],
        "performance_tests": [
            {
                "name": "Load Testing Basics",
                "category": "performance_tests",
                "description": "Standard performance benchmarks",
                "test_cases": [
                    "Response time < 500ms under normal load",
                    "Response time < 2s under peak load",
                    "Handle 100+ concurrent requests",
                    "Handle 1000+ concurrent requests",
                    "Memory usage remains stable"
                ]
            },
            {
                "name": "Stress Testing",
                "category": "performance_tests",
                "description": "Stress and endurance testing",
                "test_cases": [
                    "System stability under extreme load",
                    "Graceful degradation when overloaded",
                    "Recovery after high load subsides",
                    "No memory leaks after sustained load"
                ]
            }
        ],
        "edge_cases": [
            {
                "name": "Boundary Value Testing",
                "category": "edge_cases",
                "description": "Test boundary values and edge cases",
                "test_cases": [
                    "Empty string input",
                    "Null/None values",
                    "Maximum length input",
                    "Minimum length input",
                    "Negative numbers where applicable",
                    "Zero values",
                    "Special characters and Unicode"
                ]
            },
            {
                "name": "Concurrency Testing",
                "category": "edge_cases",
                "description": "Race condition and concurrency scenarios",
                "test_cases": [
                    "Simultaneous updates to same resource",
                    "Concurrent deletions",
                    "Race condition in state transitions",
                    "Deadlock prevention"
                ]
            }
        ]
    }


def apply_templates_to_qa_plan(qa_plan: Dict, repo: str, selected_templates: List[str]) -> Dict:
    """
    Apply selected custom templates to a QA plan.
    
    Args:
        qa_plan: Existing QA plan
        repo: Repository name
        selected_templates: List of template names to apply
        
    Returns:
        Updated QA plan with template tests added
    """
    for template_name in selected_templates:
        template = get_template(repo, template_name)
        
        if not template:
            continue
        
        category = template.get("category", "custom")
        test_cases = template.get("test_cases", [])
        
        # Add to appropriate category or create new one
        if category in qa_plan.get("test_cases", {}):
            qa_plan["test_cases"][category].extend(test_cases)
        else:
            qa_plan["test_cases"][category] = test_cases
    
    return qa_plan


def get_template_recommendations(api_name: str) -> List[Dict]:
    """
    Get recommended templates for an API based on its name.
    
    Args:
        api_name: Name of the API
        
    Returns:
        List of recommended templates
    """
    recommendations = []
    predefined = get_predefined_templates()
    
    api_lower = api_name.lower()
    
    # Always recommend these
    recommendations.append(predefined["happy_path"][0])  # Happy path tests
    recommendations.append(predefined["error_cases"][0])  # HTTP errors
    recommendations.append(predefined["security_tests"][0])  # Security
    
    # Contextual recommendations
    if any(word in api_lower for word in ["user", "auth", "login", "account", "profile"]):
        recommendations.append(predefined["security_tests"][1])  # Auth tests
    
    if any(word in api_lower for word in ["payment", "order", "transaction", "billing"]):
        recommendations.append(predefined["performance_tests"][0])  # Load testing
    
    if any(word in api_lower for word in ["search", "query", "filter", "list"]):
        recommendations.append(predefined["edge_cases"][0])  # Boundary tests
    
    return recommendations
