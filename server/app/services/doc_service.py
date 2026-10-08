from app.services.doc_generator import APIDocGenerator
from app.services.markdown_builder import MarkdownBuilder
from app.services.markdown_writer import MarkdownWriter
from app.services.version_service import VersionService
import json
from app.services.signature_service import SignatureService
from app.services import docs_store

signature_service = SignatureService()
generator=APIDocGenerator()
markdown_builder = MarkdownBuilder()
markdown_writer = MarkdownWriter()
version_service = VersionService()
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


def process_routes(routes, commit, repository):

    for route in routes:
        api_name = doc_name(route)
        print(f"Processing {api_name}...")
        signature = signature_service.generate(route)

        should_create, version = version_service.should_create_version(
            repository,
            api_name,
            signature
        )

        if not should_create:
            print(f"[SKIPPED] {api_name}")
            continue
        
        print(f"[PROCESSING] {api_name} - Version: {version}")
        explanation_raw = generator.generate_explanation(route)

        try:
            explanation = json.loads(explanation_raw)

            if isinstance(explanation, str):
                explanation = json.loads(explanation)

        except Exception:
            explanation = {
                "overview": "LLM parsing failed",
                "business_logic": "",
                "change_impact": ""
            }

        explanation["title"] = _stable_title(repository, api_name, explanation)

        documentation = markdown_builder.build(route, explanation)

        markdown_writer.write(
            repository=repository,
            api_name=api_name,
            version=version,
            content=documentation
        )

        version_service.save_version(
            repository=repository,
            api_name=api_name,
            version=version,
            signature=signature,
            commit_hash=commit,
            content=documentation,
            route=route.model_dump(mode="json"),
            title=explanation["title"]
        )