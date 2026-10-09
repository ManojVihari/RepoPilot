"""
QA Plan Generator Service
Automatically generates comprehensive test plans, regression tests, and coverage scoring
for API documentation using local Ollama LLM.
"""
import requests
import json
from typing import Optional, Dict
from app.config import OLLAMA_URL, OLLAMA_MODEL




def generate_template_qa_plan(api_name: str) -> Dict:
    """
    Generate a template QA plan when Ollama is not available.
    Provides a realistic example structure for users to see the feature.
    
    Args:
        api_name: Name of the API
        
    Returns:
        Template QA plan structure
    """
    return {
        "test_cases": {
            "happy_path": [
                "Create valid request with all required parameters",
                "Verify successful response with correct status code (200/201)",
                "Validate all response fields are present",
                "Confirm data persisted in database"
            ],
            "edge_cases": [
                "Submit request with null/empty optional fields",
                "Test with boundary values (min/max lengths)",
                "Concurrent requests to same resource",
                "Large payload handling"
            ],
            "error_cases": [
                "Missing required field → 400 Bad Request",
                "Invalid data type → 400 Bad Request",
                "Invalid authentication token → 401 Unauthorized",
                "Resource not found → 404 Not Found",
                "Duplicate entry attempt → 409 Conflict",
                "Server error handling → 500 Internal Server Error"
            ],
            "security_tests": [
                "SQL injection in string parameters",
                "XSS attack in text fields",
                "Missing authentication on protected endpoint",
                "Authorization bypass attempt",
                "Rate limiting enforcement"
            ],
            "performance_tests": [
                "Response time < 500ms under normal load",
                "Handle 100+ concurrent requests",
                "Memory usage stable over time",
                "Database query optimization"
            ]
        },
        "coverage": {
            "happy_path_score": 80.0,
            "error_coverage_score": 85.0,
            "security_coverage_score": 75.0,
            "edge_case_score": 70.0,
            "performance_score": 60.0,
            "overall_score": 74.0,
            "total_test_count": 26,
            "breakdown": {
                "happy_path": 4,
                "edge_cases": 4,
                "error_cases": 6,
                "security": 5,
                "performance": 4
            },
            "recommendations": [
                "⚠️  Ollama is not running - showing template QA plan",
                "⚠️  Add more performance/load testing scenarios",
                "✅ Core functionality tests are adequate",
                "ℹ️  Start Ollama for AI-powered test case generation: ollama serve"
            ]
        },
        "checklist": {
            "api_name": api_name,
            "total_test_scenarios": 26,
            "checklist": [
                {
                    "category": "Core Functionality",
                    "items": [
                        "☐ Run 4 happy path tests - all must pass",
                        "☐ Run 6 error handling tests - all must pass",
                        "☐ Verify response format matches documentation"
                    ]
                },
                {
                    "category": "Security Testing",
                    "items": [
                        "☐ Run 5 security tests - all must pass",
                        "☐ Security review: No new vulnerabilities",
                        "☐ Verify authentication/authorization enforced"
                    ]
                },
                {
                    "category": "Performance Testing",
                    "items": [
                        "☐ Run 4 performance tests",
                        "☐ No regression > 10% from baseline",
                        "☐ Load test passed (100+ concurrent)"
                    ]
                },
                {
                    "category": "Sign-Off",
                    "items": [
                        "☐ All test categories passed",
                        "☐ No critical/high severity bugs open",
                        "☐ Documentation updated",
                        "☐ QA lead approval",
                        "☐ Ready for production deployment"
                    ]
                }
            ],
            "estimated_testing_hours": 3.0
        },
        "regression_plan": None,
        "is_version_upgrade": False,
        "is_template": True
    }


def generate_test_cases(api_name: str, api_doc: str) -> Optional[Dict]:
    """
    Generate comprehensive test cases from API documentation.
    
    Args:
        api_name: Name of the API
        api_doc: Markdown documentation content
        
    Returns:
        Dictionary containing test cases organized by category
    """
    # Trim doc to reasonable size
    doc_preview = api_doc[:2000]
    
    prompt = f"""You are a senior QA engineer. Generate comprehensive test cases for this API.

API NAME: {api_name}

DOCUMENTATION:
{doc_preview}

Return ONLY valid JSON in this format:
{{
  "happy_path": [
    "Description of test 1 (what it validates)",
    "Description of test 2 (what it validates)"
  ],
  "edge_cases": [
    "Empty/null field handling",
    "Boundary conditions (min/max values)",
    "Concurrent request handling"
  ],
  "error_cases": [
    "Missing required field → should return 400",
    "Invalid data type → should return 400",
    "Authentication failure → should return 401",
    "Not found → should return 404",
    "Conflict/duplicate → should return 409"
  ],
  "security_tests": [
    "SQL injection in string fields",
    "XSS in text fields",
    "Unauthorized access without token",
    "Insufficient permissions check"
  ],
  "performance_tests": [
    "Response time < 500ms for normal load",
    "Handle 100+ concurrent requests"
  ]
}}

IMPORTANT:
- Be specific and actionable (not generic)
- Include expected outcomes
- Cover all parameters and error codes mentioned in docs
- At least 3 tests per category"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=180
        )
        
        response.raise_for_status()
        result = response.json()
        response_text = result.get("response", "").strip()
        
        if not response_text:
            return None
        
        # Parse JSON response
        try:
            test_cases = json.loads(response_text)
            return test_cases
        except json.JSONDecodeError:
            # Try to extract JSON from response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    test_cases = json.loads(response_text[start:end])
                    return test_cases
                except:
                    return None
        
    except requests.exceptions.ConnectionError:
        print("❌ Ollama not running. Start with: ollama serve")
        return None
    except Exception as e:
        print(f"⚠️ Error generating test cases: {e}")
        return None


def generate_regression_tests(api_name: str, v1_doc: str, v2_doc: str) -> Optional[Dict]:
    """
    Generate regression tests by comparing v1 and v2 documentation.
    
    Args:
        api_name: Name of the API
        v1_doc: Version 1 markdown documentation
        v2_doc: Version 2 markdown documentation
        
    Returns:
        Dictionary containing regression test strategy
    """
    # Trim docs
    v1_preview = v1_doc[:1500]
    v2_preview = v2_doc[:1500]
    
    prompt = f"""You are a QA specialist analyzing API version changes. Create regression test strategy.

API NAME: {api_name}

VERSION 1 DOCS:
{v1_preview}

VERSION 2 DOCS:
{v2_preview}

Return ONLY valid JSON:
{{
  "breaking_changes": [
    "Description of breaking change 1",
    "Description of breaking change 2"
  ],
  "backward_compatibility_tests": [
    "Test that v1 clients can still call the API with v1 payloads",
    "Test v1 response format compatibility (if applicable)"
  ],
  "new_feature_tests": [
    "Description of test for new feature 1",
    "Description of test for new feature 2"
  ],
  "migration_tests": [
    "Test migration path from v1 to v2",
    "Test graceful degradation for v1 clients"
  ],
  "regression_risks": [
    "Identify specific risk area 1 and why",
    "Identify specific risk area 2 and why"
  ],
  "dependent_api_impact": [
    "Identify APIs that might be affected",
    "What to test in dependent services"
  ]
}}

IMPORTANT:
- Identify ALL breaking changes
- Be specific about backward compatibility needs
- List all new features that need testing
- Flag dependent services that need regression testing"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=60
        )
        
        response.raise_for_status()
        result = response.json()
        response_text = result.get("response", "").strip()
        
        if not response_text:
            return None
        
        try:
            regression_plan = json.loads(response_text)
            return regression_plan
        except json.JSONDecodeError:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    regression_plan = json.loads(response_text[start:end])
                    return regression_plan
                except:
                    return None
        
    except requests.exceptions.ConnectionError:
        print("❌ Ollama not running. Start with: ollama serve")
        return None
    except Exception as e:
        print(f"⚠️ Error generating regression tests: {e}")
        return None


def calculate_coverage_score(test_cases: Dict) -> Dict:
    """
    Calculate test coverage score across different dimensions.
    
    Args:
        test_cases: Dictionary of test cases from generate_test_cases()
        
    Returns:
        Dictionary with coverage metrics and recommendations
    """
    if not test_cases:
        return {
            "happy_path_score": 0,
            "error_coverage_score": 0,
            "security_coverage_score": 0,
            "overall_score": 0,
            "recommendations": ["Failed to generate test cases"]
        }
    
    recommendations = []
    
    # Count tests in each category
    happy_path_count = len(test_cases.get("happy_path", []))
    edge_case_count = len(test_cases.get("edge_cases", []))
    error_count = len(test_cases.get("error_cases", []))
    security_count = len(test_cases.get("security_tests", []))
    perf_count = len(test_cases.get("performance_tests", []))
    
    # Happy path score (should have 2-4 tests)
    happy_path_score = min(100, (happy_path_count / 3) * 100)
    
    # Error coverage score
    error_score = min(100, (error_count / 5) * 100)
    if error_count < 4:
        recommendations.append(f"⚠️ Add more error case tests ({error_count}/5 minimum)")
    
    # Security coverage score
    security_score = min(100, (security_count / 4) * 100)
    if security_count < 3:
        recommendations.append(f"⚠️ Add more security tests ({security_count}/4 recommended)")
    
    # Edge case coverage
    edge_score = min(100, (edge_case_count / 4) * 100)
    if edge_case_count < 3:
        recommendations.append(f"⚠️ Add more edge case tests ({edge_case_count}/4 recommended)")
    
    # Performance tests
    if perf_count == 0:
        recommendations.append("⚠️ Add performance/load testing scenarios")
    
    # Overall score
    total_tests = happy_path_count + edge_case_count + error_count + security_count + perf_count
    overall_score = (happy_path_score * 0.2 + error_score * 0.3 + security_score * 0.25 + edge_score * 0.15 + (min(100, (perf_count / 2) * 100) * 0.1))
    
    if not recommendations:
        recommendations.append("✅ Comprehensive test coverage - ready for QA execution")
    
    return {
        "happy_path_score": round(happy_path_score, 1),
        "error_coverage_score": round(error_score, 1),
        "security_coverage_score": round(security_score, 1),
        "edge_case_score": round(edge_score, 1),
        "performance_score": round(min(100, (perf_count / 2) * 100), 1),
        "overall_score": round(overall_score, 1),
        "total_test_count": total_tests,
        "breakdown": {
            "happy_path": happy_path_count,
            "edge_cases": edge_case_count,
            "error_cases": error_count,
            "security": security_count,
            "performance": perf_count
        },
        "recommendations": recommendations
    }


def generate_qa_execution_checklist(api_name: str, test_cases: Dict, regression_plan: Optional[Dict] = None) -> Dict:
    """
    Generate a QA execution checklist for deployment.
    
    Args:
        api_name: API name
        test_cases: Test cases dictionary
        regression_plan: Regression plan dictionary (optional)
        
    Returns:
        Execution checklist dictionary
    """
    checklist_items = []
    
    # Unit/Integration tests
    happy_path_count = len(test_cases.get("happy_path", []))
    error_count = len(test_cases.get("error_cases", []))
    total = happy_path_count + error_count
    
    checklist_items.append({
        "category": "Core Functionality",
        "items": [
            f"☐ Run {happy_path_count} happy path tests - all must pass",
            f"☐ Run {error_count} error handling tests - all must pass",
            "☐ Verify response format matches documentation"
        ]
    })
    
    # Security
    security_count = len(test_cases.get("security_tests", []))
    if security_count > 0:
        checklist_items.append({
            "category": "Security Testing",
            "items": [
                f"☐ Run {security_count} security tests - all must pass",
                "☐ Security review: No new vulnerabilities",
                "☐ Verify authentication/authorization enforced"
            ]
        })
    
    # Performance
    perf_count = len(test_cases.get("performance_tests", []))
    if perf_count > 0:
        checklist_items.append({
            "category": "Performance Testing",
            "items": [
                f"☐ Run {perf_count} performance tests",
                "☐ No regression > 10% from baseline",
                "☐ Load test passed (100+ concurrent)"
            ]
        })
    
    # Regression (if v1→v2)
    if regression_plan:
        breaking_changes = len(regression_plan.get("breaking_changes", []))
        if breaking_changes > 0:
            checklist_items.append({
                "category": "Regression Testing (Breaking Changes)",
                "items": [
                    f"☐ {breaking_changes} breaking changes identified",
                    "☐ Backward compatibility tests passed",
                    "☐ Migration guide reviewed with stakeholders",
                    "☐ Dependent services identified and tested"
                ]
            })
    
    # Final sign-off
    checklist_items.append({
        "category": "Sign-Off",
        "items": [
            "☐ All test categories passed",
            "☐ No critical/high severity bugs open",
            "☐ Documentation updated",
            "☐ QA lead approval",
            "☐ Ready for production deployment"
        ]
    })
    
    return {
        "api_name": api_name,
        "total_test_scenarios": total,
        "checklist": checklist_items,
        "estimated_testing_hours": max(2, (total / 10))  # Rough estimate: 10 tests per hour
    }


def generate_full_qa_plan(api_name: str, api_doc: str, v1_doc: Optional[str] = None) -> Dict:
    """
    Generate complete QA plan for an API.
    
    Args:
        api_name: API name
        api_doc: Current API documentation
        v1_doc: Previous version documentation (optional, for regression)
        
    Returns:
        Complete QA plan with all components
    """
    # Step 1: Generate test cases
    test_cases = generate_test_cases(api_name, api_doc)
    
    # If Ollama is not available, use template
    if not test_cases:
        print(f"⚠️ Ollama not available for {api_name}, using template QA plan")
        return generate_template_qa_plan(api_name)
    
    # Step 2: Generate regression tests if v1 provided
    regression_plan = None
    if v1_doc:
        regression_plan = generate_regression_tests(api_name, v1_doc, api_doc)
    
    # Step 3: Calculate coverage score
    coverage = calculate_coverage_score(test_cases)
    
    # Step 4: Generate execution checklist
    checklist = generate_qa_execution_checklist(api_name, test_cases, regression_plan)
    
    # Compile full plan
    full_plan = {
        "api_name": api_name,
        "test_cases": test_cases,
        "coverage": coverage,
        "checklist": checklist,
        "regression_plan": regression_plan,
        "is_version_upgrade": v1_doc is not None,
        "is_template": False
    }
    
    return full_plan
