from app.services.doc_generator import APIDocGenerator
from app.services.markdown_builder import MarkdownBuilder
from app.services.markdown_writer import MarkdownWriter
from app.services.version_service import VersionService
import json
from app.services.signature_service import SignatureService

signature_service = SignatureService()
generator=APIDocGenerator()
markdown_builder = MarkdownBuilder()
markdown_writer = MarkdownWriter()
version_service = VersionService()
def process_routes(routes, commit, repository):

    for route in routes:
        print(f"Processing {route.function}...")
        signature = signature_service.generate(route)

        should_create, version = version_service.should_create_version(
            repository,
            route.function,
            signature
        )

        if not should_create:
            print(f"[SKIPPED] {route.function}")
            continue
        
        print(f"[PROCESSING] {route.function} - Version: {version}")
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

        documentation = markdown_builder.build(route, explanation)

        markdown_writer.write(
            repository=repository,
            api_name=route.function,
            version=version,
            content=documentation
        )

        version_service.save_version(
            repository=repository,
            api_name=route.function,
            version=version,
            signature=signature,
            commit_hash=commit,
            content=documentation,
            route=route.model_dump(mode="json")
        )