"""
Custom Test Templates Service
Allows organizations to define reusable test case patterns
"""
import os
import json
from typing import Dict, List, Optional
from datetime import datetime
from app.config import DATABASE_DIR
from app.services.docs_store import is_safe_name


TEMPLATES_DIR = os.path.join(DATABASE_DIR, "test_templates")


def _template_filename(template_name: str) -> str:
    return f"{template_name.lower().replace(' ', '_').replace('-', '_')}.json"


def _template_path(repo: str, template_name: str) -> Optional[str]:
    """Path of a template file, or None if repo/name would escape TEMPLATES_DIR."""
    filename = _template_filename(template_name)

    if not (is_safe_name(repo) and is_safe_name(filename)):
        return None

    return os.path.join(TEMPLATES_DIR, repo, filename)


def create_template(repo: str, name: str, category: str, description: str, test_cases: List[str]) -> bool:
    """
    Create a new custom test template for an organization.
    
    Args:
        repo: Repository/organization name
        name: Template name (e.g., "Payment Flow Tests")
        category: Category (happy_path, error_cases, security_tests, etc.)
        description: Template description
        test_cases: List of test case descriptions
        
    Returns:
        True if successful, False otherwise
    """
    template_path = _template_path(repo, name)

    if not template_path:
        return False

    os.makedirs(os.path.dirname(template_path), exist_ok=True)
    
    template = {
        "name": name,
        "category": category,
        "description": description,
        "test_cases": test_cases,
        "created_at": datetime.utcnow().isoformat(),
        "test_count": len(test_cases)
    }
    
    try:
        with open(template_path, 'w') as f:
            json.dump(template, f, indent=2)
        return True
    except Exception as e:
        print(f"❌ Error creating template: {e}")
        return False


def get_template(repo: str, template_name: str) -> Optional[Dict]:
    """Get a specific template."""
    template_path = _template_path(repo, template_name)

    if not template_path or not os.path.exists(template_path):
        return None
    
    try:
        with open(template_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error reading template: {e}")
        return None


def list_templates(repo: str, category: Optional[str] = None) -> List[Dict]:
    """
    List all templates for a repository, optionally filtered by category.
    
    Args:
        repo: Repository name
        category: Optional category filter
        
    Returns:
        List of templates
    """
    if not is_safe_name(repo):
        return []

    repo_dir = os.path.join(TEMPLATES_DIR, repo)
    
    if not os.path.exists(repo_dir):
        return []
    
    templates = []
    
    try:
        for filename in os.listdir(repo_dir):
            if not filename.endswith('.json'):
                continue
            
            template_path = os.path.join(repo_dir, filename)
            with open(template_path, 'r') as f:
                template = json.load(f)
                
                if category is None or template.get("category") == category:
                    templates.append(template)
    except Exception as e:
        print(f"❌ Error listing templates: {e}")
    
    return sorted(templates, key=lambda x: x.get("created_at", ""), reverse=True)


def delete_template(repo: str, template_name: str) -> bool:
    """Delete a template."""
    template_path = _template_path(repo, template_name)

    if not template_path or not os.path.exists(template_path):
        return False
    
    try:
        os.remove(template_path)
        return True
    except Exception as e:
        print(f"❌ Error deleting template: {e}")
        return False


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
