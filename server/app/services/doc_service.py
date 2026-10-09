import json
import logging

from app.services import docs_store
from app.services.doc_generator import APIDocGenerator
from app.services.markdown_builder import MarkdownBuilder
from app.services.signature_service import SignatureService

logger = logging.getLogger(__name__)

signature_service = SignatureService()
generator = APIDocGenerator()
markdown_builder = MarkdownBuilder()


def doc_name(route):
    """
    Name the docs of a route are stored under.

    Spring routes use Controller.method so equally named handlers of
    different controllers (list, create, ...) do not overwrite each other.
    """
    return getattr(route, "doc_name", None) or getattr(route, "handler", None) or route.function


def _stable_title(repository, api_name, explanation):
    """
    Display title of a doc. A title written by the LLM is kept for later
    versions so pages are not renamed on every change; a fallback title is
    replaced as soon as the LLM provides one.
    """
    existing = docs_store.title_entry(repository, api_name)
    new_title = explanation.get("title") or api_name
    new_source = explanation.get("title_source", "fallback")

    if existing and existing.get("title") and (existing.get("source") == "llm" or new_source != "llm"):
        return existing["title"]

    docs_store.set_title(repository, api_name, new_title, new_source)
    return new_title


def _explain(route) -> dict:
    raw = generator.generate_explanation(route)
    try:
        explanation = json.loads(raw)
        if isinstance(explanation, str):
            explanation = json.loads(explanation)
        if isinstance(explanation, dict):
            return explanation
    except Exception:
        pass
    return {"overview": "LLM parsing failed", "business_logic": "", "change_impact": ""}


def process_routes(routes, commit, repository, progress=None) -> dict:
    """
    Document every route whose contract changed since its latest version.

    `progress(done, total)` is called after each route. Returns counts:
    {"created": n, "unchanged": n}.
    """
    created = unchanged = 0
    total = len(routes)

    for done, route in enumerate(routes, start=1):
        api_name = doc_name(route)
        signature = signature_service.generate(route)

        # cheap check first: skip the LLM when the contract is unchanged
        if docs_store.latest_signature(repository, api_name) == signature:
            unchanged += 1
        else:
            explanation = _explain(route)
            explanation["title"] = _stable_title(repository, api_name, explanation)
            documentation = markdown_builder.build(route, explanation)

            # decided again under the per-API lock: another worker may have stored it meanwhile
            version = docs_store.save_version_if_changed(
                repository, api_name, signature, commit, documentation,
                route=route.model_dump(mode="json"), title=explanation["title"],
            )
            if version is None:
                unchanged += 1
            else:
                created += 1
                logger.info("documented %s/%s v%d", repository, api_name, version)

        if progress:
            progress(done, total)

    return {"created": created, "unchanged": unchanged}
