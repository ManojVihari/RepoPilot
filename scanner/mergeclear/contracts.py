"""
API contract comparison, shared by the CLI (`mergeclear diff` / `check`) and
the server. Works on the route dicts of scan reports; no server needed.
"""
from typing import Dict, List, Optional

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


def change(kind, severity, detail, **extra):
    """One contract difference: type code, severity (breaking|minor|additive) and a readable detail."""
    return {"type": kind, "severity": severity, "detail": detail, **extra}


def contract_changes(old: dict, new: dict) -> List[dict]:
    """Contract differences between two versions of an endpoint."""
    changes = []

    if (old.get("method"), old.get("path")) != (new.get("method"), new.get("path")):
        changes.append(change("ENDPOINT_CHANGED", "breaking",
                               f"{old.get('method')} {old.get('path')} → {new.get('method')} {new.get('path')}"))

    # ---- parameters (path / query / header / cookie) ----
    def params(route):
        return {(p.get("in"), p.get("name")): p for p in route.get("params") or [] if p.get("in") not in ("body", "model")}

    old_params, new_params = params(old), params(new)
    for key, p in new_params.items():
        location, name = key
        if key not in old_params:
            severity = "breaking" if p.get("required") else "additive"
            changes.append(change("PARAM_ADDED", severity, f"{location} parameter `{name}` ({p.get('type')})"
                                   + (" is required" if p.get("required") else " (optional)")))
            continue
        before = old_params[key]
        if before.get("type") != p.get("type"):
            changes.append(change("PARAM_TYPE_CHANGED", "breaking", f"{location} parameter `{name}`: {before.get('type')} → {p.get('type')}"))
        if p.get("required") and not before.get("required"):
            changes.append(change("PARAM_NOW_REQUIRED", "breaking", f"{location} parameter `{name}` became required"))
    for key, p in old_params.items():
        if key not in new_params:
            location, name = key
            changes.append(change("PARAM_REMOVED", "breaking" if location == "path" else "minor",
                                   f"{location} parameter `{name}` removed"))

    # ---- request body ----
    old_body, new_body = old.get("request_body") or {}, new.get("request_body") or {}
    if old_body.get("type") != new_body.get("type") and (old_body or new_body):
        changes.append(change("REQUEST_BODY_CHANGED", "breaking", f"request body {old_body.get('type')} → {new_body.get('type')}"))

    old_fields, new_fields = _flatten_schema(old_body.get("schema")), _flatten_schema(new_body.get("schema"))
    for field, details in new_fields.items():
        before = old_fields.get(field)
        if before is None:
            required = bool(details["validation"].get("required"))
            changes.append(change("REQUEST_FIELD_ADDED", "breaking" if required else "additive",
                                   f"`{field}` ({details['type']})" + (" is required" if required else " (optional)")))
            continue
        if before["type"] != details["type"]:
            changes.append(change("REQUEST_FIELD_TYPE_CHANGED", "breaking", f"`{field}`: {before['type']} → {details['type']}"))
        for rule, value in details["validation"].items():
            if rule not in before["validation"]:
                changes.append(change("VALIDATION_ADDED", "breaking", f"`{field}` now validated: {rule}={value}"))
            elif before["validation"][rule] != value:
                changes.append(change("VALIDATION_CHANGED", "breaking", f"`{field}` {rule}: {before['validation'][rule]} → {value}"))
        for rule in before["validation"]:
            if rule not in details["validation"]:
                changes.append(change("VALIDATION_REMOVED", "minor", f"`{field}` no longer validated: {rule}"))
    for field in old_fields:
        if field not in new_fields:
            changes.append(change("REQUEST_FIELD_REMOVED", "minor", f"`{field}` is no longer read"))

    # ---- response ----
    old_resp, new_resp = old.get("response") or {}, new.get("response") or {}
    if old_resp.get("body_type") != new_resp.get("body_type") and (old_resp.get("body_type") or new_resp.get("body_type")):
        changes.append(change("RESPONSE_TYPE_CHANGED", "breaking", f"{old_resp.get('body_type')} → {new_resp.get('body_type')}"))

    old_out, new_out = _response_fields(old), _response_fields(new)
    for field, details in old_out.items():
        if field not in new_out:
            changes.append(change("RESPONSE_FIELD_REMOVED", "breaking", f"`{field}` is no longer returned"))
        elif new_out[field]["type"] != details["type"]:
            changes.append(change("RESPONSE_FIELD_TYPE_CHANGED", "breaking", f"`{field}`: {details['type']} → {new_out[field]['type']}"))
    for field, details in new_out.items():
        if field not in old_out:
            changes.append(change("RESPONSE_FIELD_ADDED", "additive", f"`{field}` ({details['type']})"))

    # ---- status codes ----
    old_codes = {s.get("code") for s in old.get("status_codes") or []}
    new_codes = {s.get("code") for s in new.get("status_codes") or []}
    old_success = {c for c in old_codes if c and c < 300}
    new_success = {c for c in new_codes if c and c < 300}
    if old_success and new_success and old_success != new_success:
        changes.append(change("SUCCESS_STATUS_CHANGED", "breaking", f"{sorted(old_success)} → {sorted(new_success)}"))
    for code in sorted(c for c in new_codes - old_codes if c and c >= 400):
        changes.append(change("ERROR_STATUS_ADDED", "minor", f"may now return {code}"))
    for code in sorted(c for c in old_codes - new_codes if c and c >= 400):
        changes.append(change("ERROR_STATUS_REMOVED", "additive", f"no longer returns {code}"))

    # ---- security ----
    def security(route):
        sec = route.get("security") or {}
        annotations = sorted(f"{a.get('annotation')}:{a.get('expression')}" for a in sec.get("annotations") or [])
        rule = sec.get("url_rule") or {}
        return annotations, (rule.get("access"), tuple(rule.get("roles") or []))

    if security(old) != security(new):
        changes.append(change("SECURITY_CHANGED", "breaking", "access rules changed",
                               old=security(old)[0] or None, new=security(new)[0] or None))

    rank = {"breaking": 0, "minor": 1, "additive": 2}
    return sorted(changes, key=lambda c: rank.get(c["severity"], 3))


def _downstream_entries(route: dict) -> Dict[str, Dict[str, dict]]:
    """What an endpoint reaches, per group: display key -> structured entry."""
    integrations = (route or {}).get("integrations") or {}
    groups: Dict[str, Dict[str, dict]] = {g: {} for g in ("databases", "caches", "messaging", "external_apis", "other")}

    def put(group, key, operation, name, technology):
        groups[group][key] = {"operation": (operation or "").lower(), "name": name, "technology": technology or ""}

    for f in integrations.get("databases", []):
        for t in (f.get("table") or [f.get("entity") or "?"]):
            put("databases", f"{f.get('operation')} {t} ({f.get('technology')})", f.get("operation"), t, f.get("technology"))
    for f in integrations.get("caches", []):
        name = ", ".join(f.get("cache_names") or []) or f.get("key") or f.get("technology")
        put("caches", f"{f.get('operation')} {name} ({f.get('technology')})", f.get("operation"), name, f.get("technology"))
    for f in integrations.get("messaging", []):
        put("messaging", f"{f.get('direction')} {f.get('destination')} ({f.get('technology')})",
            f.get("direction"), str(f.get("destination")), f.get("technology"))
    for f in integrations.get("external_apis", []):
        target = f.get("target_service") or f.get("host")
        key = f"{f.get('http_method') or ''} {f.get('url') or ''}".strip() + (f" → {target}" if target else "")
        put("external_apis", key, f.get("http_method"), target or f.get("url") or "unresolved",
            f.get("url") if target else "")
    for group in ("events", "storage", "email", "search"):
        for f in integrations.get(group, []):
            name = f.get("event_type") or f.get("bucket") or f.get("technology")
            put("other", f"{group}: {name}", group.rstrip("s") if group == "events" else group, name, f.get("technology") if name != f.get("technology") else "")
    return groups


def downstream(route: dict) -> Dict[str, List[str]]:
    """Compact view of what an endpoint reaches: tables, caches, topics, external calls."""
    return {group: sorted(entries) for group, entries in _downstream_entries(route).items()}


def dependency_items(old: Optional[dict], new: Optional[dict]) -> Dict[str, List[dict]]:
    """Structured downstream dependencies of the new version, marked new/removed against the old one."""
    after = _downstream_entries(new)
    before = _downstream_entries(old) if old else None
    out = {}
    for group, entries in after.items():
        items = [{**e, "status": "new" if before is not None and key not in before[group] else "same"}
                 for key, e in sorted(entries.items())]
        if before is not None:
            items += [{**e, "status": "removed"} for key, e in sorted(before[group].items()) if key not in entries]
        if items:
            out[group] = items
    return out


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


# ============================================================================
# VERDICTS
# ============================================================================
# Clear: nothing a client could notice (or only additions).
# Review: behaviour a client may notice (new error codes, removed optional
#         input, new or removed downstream systems).
# Hold: breaking contract changes; clients fail until they are updated.

CLEAR, REVIEW, HOLD = "clear", "review", "hold"
VERDICT_ORDER = {CLEAR: 0, REVIEW: 1, HOLD: 2}


def verdict(changes: List[dict], downstream_delta: Optional[dict] = None) -> str:
    if any(c.get("severity") == "breaking" for c in changes):
        return HOLD
    if any(c.get("severity") == "minor" for c in changes) or downstream_delta:
        return REVIEW
    return CLEAR


def route_key(route: dict) -> str:
    """Stable name of an endpoint across scans (the server stores its docs under it)."""
    return route.get("doc_name") or route.get("handler") or route.get("function") or f"{route.get('method')} {route.get('path')}"


def compare_reports(base: dict, head: dict) -> dict:
    """
    Endpoint-by-endpoint differences between two scan reports.

    Removed endpoints are only reported when both scans are full scans: an
    incremental scan lists just the endpoints its commit touched.
    """
    base_routes = {route_key(r): r for r in base.get("routes") or []}
    head_routes = {route_key(r): r for r in head.get("routes") or []}
    complete = base.get("scan_mode", "full") == "full" and head.get("scan_mode", "full") == "full"

    endpoints = []
    for key in sorted(set(base_routes) | set(head_routes)):
        old, new = base_routes.get(key), head_routes.get(key)
        if old is None:
            entry = {"status": "added", "changes": [change("ENDPOINT_ADDED", "additive", f"{new.get('method')} {new.get('path')}")],
                     "dependencies": {}}
        elif new is None:
            if not complete:
                continue
            entry = {"status": "removed", "changes": [change("ENDPOINT_REMOVED", "breaking", f"{old.get('method')} {old.get('path')}")],
                     "dependencies": {}}
        else:
            changes = contract_changes(old, new)
            delta = downstream_changes(old, new)
            if not changes and not delta:
                continue
            entry = {"status": "changed", "changes": changes, "dependencies": delta}
        route = new or old
        entry.update({"name": key, "method": route.get("method"), "path": route.get("path"),
                      "verdict": verdict(entry["changes"], entry["dependencies"])})
        endpoints.append(entry)

    endpoints.sort(key=lambda e: (-VERDICT_ORDER[e["verdict"]], e["name"]))
    overall = max((e["verdict"] for e in endpoints), key=VERDICT_ORDER.get, default=CLEAR)
    counts = {s: sum(1 for e in endpoints for c in e["changes"] if c["severity"] == s) for s in ("breaking", "minor", "additive")}

    return {
        "verdict": overall,
        "base": {"commit": base.get("commit"), "branch": base.get("branch")},
        "head": {"commit": head.get("commit"), "branch": head.get("branch")},
        "complete": complete,
        "summary": {"endpoints_compared": len(set(base_routes) & set(head_routes)), "endpoints_changed": len(endpoints), **counts},
        "endpoints": endpoints,
    }
