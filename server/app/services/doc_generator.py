import requests
import json
from app.config import LLM_ENABLED, OLLAMA_URL, OLLAMA_MODEL


class APIDocGenerator:

    def __init__(self, model=OLLAMA_MODEL):
        self.model = model
        self.ollama_url = OLLAMA_URL

    def generate_explanation(self, route):

        # =========================
        # 🔥 SAFE EXTRACTION
        # =========================
        function_name = getattr(route, "function_name", None) or getattr(route, "function", "unknown")
        method = getattr(route, "method", "")
        path = getattr(route, "path", "")
        parameters = getattr(route, "parameters", None) or getattr(route, "params", [])
        calls = getattr(route, "calls", [])
        call_graph = getattr(route, "call_graph", {})
        response = getattr(route, "response", {})
        db_ops = getattr(route, "db_ops", [])
        source_code = getattr(route, "source_code", "")
        description = getattr(route, "description", None) or ""
        integrations = getattr(route, "integrations", None) or {}
        status_codes = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in (getattr(route, "status_codes", None) or [])
        ]
        security = getattr(route, "security", None) or {}

        # =========================
        # 🔥 NORMALIZATION
        # =========================
        def safe_json(data):
            try:
                return json.dumps(data, default=lambda o: o.__dict__, indent=2)
            except Exception:
                return str(data)

        parameters_json = safe_json(parameters)
        call_graph_json = safe_json(call_graph)
        response_json = safe_json(response)
        db_ops_json = safe_json(db_ops)
        integrations_json = safe_json(integrations)
        status_codes_json = safe_json(status_codes)
        security_json = safe_json(security)

        # =========================
        # 🔥 STRONG BUSINESS PROMPT
        # =========================
        prompt = f"""
You are a senior backend engineer creating INTERNAL API documentation.

IMPORTANT:
- Write in a professional, business-oriented tone
- DO NOT mention frameworks (Spring, Java, etc.)
- Focus on system behavior and business purpose
- Ignore getters, setters, logging, trivial helpers

GOAL:
Convert technical details into meaningful system-level documentation.

---

Return ONLY valid JSON:

{{
  "overview": "What this API does and why it exists (business purpose)",
  "business_logic": "How the system processes the request internally (clear explanation)",
  "business_flow": ["Step 1...", "Step 2...", "Step 3..."],
  "response_description": "What the client receives (business meaning, not code)",
  "change_impact": "Who/what is affected if this API changes"
}}

---

API CONTEXT

Function: {function_name}
Method: {method}
Path: {path}

Parameters:
{parameters_json}

Calls:
{calls}

Call Graph:
{call_graph_json}

Database Operations:
{db_ops_json}

Downstream integrations (databases, caches, messaging, external APIs reached through the call chain):
{integrations_json}

Status codes:
{status_codes_json}

Security:
{security_json}

Developer description:
{description}

Response:
{response_json}

Source Code:
{source_code}
"""

        # =========================
        # 🔥 LLM CALL + RETRY
        # =========================
        for attempt in range(2 if LLM_ENABLED else 0):  # retry once if parsing fails
            try:
                response = requests.post(
                    self.ollama_url,
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False
                    },
                    timeout=60
                )

                output = response.json().get("response", "").strip()

                # 🔥 CLEAN JSON
                if "{" in output and "}" in output:
                    output = output[output.find("{"): output.rfind("}") + 1]

                parsed = json.loads(output)

                # 🔥 BASIC VALIDATION
                if "overview" in parsed and "business_flow" in parsed:
                    return json.dumps(parsed)

            except Exception as e:
                print(f"LLM ERROR (attempt {attempt+1}):", e)

        # =========================
        # 🔥 FALLBACK (SMART)
        # =========================
        direct_calls = (call_graph or {}).get("direct") if isinstance(call_graph, dict) else None
        summary = getattr(route, "summary", None)

        return json.dumps({
            "overview": summary or description or f"Provides functionality for {function_name}",
            "business_logic": "Processes request and interacts with underlying system components",
            "business_flow": [
                f"Invoke {c}" for c in (direct_calls or calls)
            ] if (direct_calls or calls) else [],
            "response_description": "Returns result based on request processing",
            "change_impact": "Changes may affect dependent services and clients"
        })