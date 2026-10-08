import json


class MarkdownBuilder:

    def build(self, route_data, llm_sections):

        # 🔥 SAFE NAME
        api_name = getattr(route_data, "function_name", None) or getattr(route_data, "function", "unknown")

        md = f"# API: {api_name}\n\n"

        # =========================
        # 🔥 Overview
        # =========================
        md += "## Overview\n"
        md += llm_sections.get("overview", "No overview available.") + "\n\n"

        # =========================
        # 🔥 Endpoint
        # =========================
        md += "## Endpoint\n"
        md += f"- **Method:** {route_data.method}\n"
        md += f"- **Path:** `{route_data.path}`\n\n"

        # =========================
        # 🔥 Request (DTO Structured)
        # =========================
        parameters = getattr(route_data, "parameters", None) or getattr(route_data, "params", [])

        if parameters:
            md += "## Request\n\n"

            for param in parameters:

                name = getattr(param, "name", param.get("name"))
                ptype = getattr(param, "type", param.get("type"))
                schema = getattr(param, "schema", param.get("schema", {}))

                md += f"### {name} ({ptype})\n\n"

                if schema:
                    md += "| Field | Type | Required | Validation |\n"
                    md += "|------|------|----------|------------|\n"

                    for field, details in schema.items():
                        ftype = details.get("type")
                        validation = details.get("validation", {})

                        rules = ", ".join(validation.keys()) if validation else "-"
                        req = "Yes" if validation.get("required") else "No"

                        md += f"| {field} | {ftype} | {req} | {rules} |\n"

                md += "\n"

        # =========================
        # 🔥 Response (FIXED)
        # =========================
        response = getattr(route_data, "response", {})

        md += "## Response\n\n"

        if response.get("type"):
            md += f"- **Type:** {response.get('type')}\n"

        # 🔥 Prefer structured schema
        if isinstance(response.get("schema"), dict):
            md += "- **Schema:**\n"
            md += "```json\n"
            md += json.dumps(response["schema"], indent=2)
            md += "\n```\n\n"

        else:
            # 🔥 DO NOT show internal method call
            desc = llm_sections.get("response_description")

            if desc:
                md += f"- **Description:** {desc}\n\n"
            else:
                md += "- **Description:** Returns result of operation\n\n"

        # =========================
        # 🔥 Errors
        # =========================
        errors = getattr(route_data, "status_codes", None) or getattr(route_data, "errors", [])

        if errors:
            md += "## Errors\n\n"
            md += "| Code | Field | Rule |\n"
            md += "|------|------|------|\n"

            for err in errors:
                if not isinstance(err, dict):
                    # plain status code (404) or HttpStatus name (NOT_FOUND)
                    md += f"| {err} | - | - |\n"
                    continue

                fields = err.get("fields", [])

                for f in fields:
                    md += f"| {err.get('status')} | {f.get('name')} | {f.get('rule')} |\n"

            md += "\n"

        # =========================
        # 🔥 Process Flow (KEY UPGRADE)
        # =========================
        flow = llm_sections.get("business_flow", [])

        if not flow:
            # fallback using AST calls
            direct = getattr(route_data, "call_graph", {}).get("direct", [])
            flow = [f"Invoke {c}" for c in direct]

        if flow:
            md += "## Process Flow\n"
            for i, step in enumerate(flow, 1):
                md += f"{i}. {step}\n"
            md += "\n"

        # =========================
        # 🔥 DB Operations
        # =========================
        db_ops = getattr(route_data, "db_ops", [])

        if db_ops:
            md += "## Database Operations\n"
            for op in db_ops:
                md += f"- **{op.get('type')}** → `{op.get('call')}`\n"
            md += "\n"

        # =========================
        # 🔥 Business Logic
        # =========================
        md += "## Business Logic\n"
        md += llm_sections.get("business_logic", "No details available.") + "\n\n"

        # =========================
        # 🔥 Breaking Changes
        # =========================
        breaking = getattr(route_data, "breaking_changes", [])

        if breaking:
            md += "## Breaking Changes\n"
            for b in breaking:
                md += f"- **{b.get('type')}** → {b.get('field')} ({b.get('rule')})\n"
            md += "\n"

        # =========================
        # 🔥 Change Impact
        # =========================
        md += "## Change Impact\n"
        md += llm_sections.get("change_impact", "No impact details available.") + "\n"

        return md