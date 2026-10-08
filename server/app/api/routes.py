import difflib
from urllib.parse import quote
from datetime import datetime
from typing import List
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.config import TEMPLATES_DIR
from app.models.schema import AnalyzeRequest
from app.services import docs_store
from app.services.architecture_store import load_architecture, save_architecture
from app.services.qa_plan_service import QAPlanService
from app.services.doc_service import process_routes
from app.services.llm_service import summarize_changes, search_apis_rag, answer_question_based_on_docs
from app.services.qa_plan_generator import generate_full_qa_plan
from app.services.dependency_analyzer import get_impact_analysis, build_dependency_graph
from app.services.test_templates import (
    get_predefined_templates, list_templates, create_template,
    get_template_recommendations
)
import markdown
from bs4 import BeautifulSoup

router = APIRouter()
qa_plan_service = QAPlanService()
templates = Jinja2Templates(directory=TEMPLATES_DIR)

DOC_MARKDOWN_EXTENSIONS = [
    "tables",
    "fenced_code",
    "toc",
    "codehilite",
    "attr_list",
    "md_in_html"
]


def _nav_context(repo):
    """Sidebar data shared by the per-API pages."""
    return {
        "apis": [{"name": api} for api in docs_store.list_apis(repo)],
        "repos": docs_store.list_repos()
    }


def _repos_overview(include_version_count=False):
    data = {}

    for repo in docs_store.list_repos():
        apis = []

        for api in docs_store.list_apis(repo):
            versions = docs_store.list_versions(repo, api)
            entry = {"name": api, "latest_version": versions[-1]}

            if include_version_count:
                entry["versions"] = len(versions)

            apis.append(entry)

        if apis:
            data[repo] = apis

    return data


def _default_versions(versions, v1, v2):
    """v2 defaults to the latest version, v1 to the one before it."""
    if v2 is None:
        v2 = versions[-1]

    if v1 is None and len(versions) > 1:
        v1 = versions[-2]

    return v1, v2


@router.post("/analyze")
async def analyze(request: AnalyzeRequest, background_tasks: BackgroundTasks):

    if request.architecture:
        background_tasks.add_task(
            save_architecture,
            request.repository,
            request.commit,
            request.architecture
        )

    background_tasks.add_task(
        process_routes,
        request.routes,
        request.commit,
        request.repository
    )

    return {
        "status": "processing_started"
    }


@router.get("/api/architecture")
def get_architecture(repo: str, commit: str = None):
    """Application model (modules, components, data, integrations, graph) from the latest scan."""
    document = load_architecture(repo, commit)

    if document is None:
        return JSONResponse({"error": f"No architecture stored for '{repo}'"}, status_code=404)

    return JSONResponse(document)


@router.get("/ui", response_class=HTMLResponse)
def ui_home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"data": _repos_overview()}
    )


@router.get("/ui/all", response_class=HTMLResponse)
def ui_all_apis(request: Request):
    """Display all APIs from all repositories in a unified view"""
    return templates.TemplateResponse(
        request,
        "all_apis.html",
        {"data": _repos_overview(include_version_count=True)}
    )


def render_md(md_text):
    return markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code"]
    )

def html_to_blocks(html):
    soup = BeautifulSoup(html, "html.parser")

    blocks = []
    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "pre", "table"]):
        blocks.append(str(el))  # keep HTML intact

    return blocks

def generate_diff(content1, content2):
    diff = list(difflib.ndiff(content1, content2))

    rows = []
    left_line = 1
    right_line = 1

    buffer_removed = []

    i = 0
    while i < len(diff):
        tag = diff[i][:2]
        text = diff[i][2:]

        # SAME
        if tag == "  ":
            rows.append(("same", left_line, text, right_line, text))
            left_line += 1
            right_line += 1
            i += 1

        # REMOVED → store in buffer
        elif tag == "- ":
            buffer_removed.append((left_line, text))
            left_line += 1
            i += 1

        # ADDED → match with buffer
        elif tag == "+ ":
            if buffer_removed:
                lnum, old_text = buffer_removed.pop(0)

                # 🔥 word highlight
          # detect table diff
                if old_text.strip().startswith("<table") and text.strip().startswith("<table"):
                    old_h, new_h = highlight_table_diff(old_text, text)

                # normal text diff
                else:
                    old_h, new_h = highlight_words(old_text, text)

                rows.append(("change", lnum, old_h, right_line, new_h))
            else:
                rows.append(("add", "", "", right_line, text))

            right_line += 1
            i += 1

        else:
            i += 1

    # leftover removed lines
    for lnum, old_text in buffer_removed:
        rows.append(("remove", lnum, old_text, "", ""))

    return rows

def highlight_table_diff(old_html, new_html):
    old_soup = BeautifulSoup(old_html, "html.parser")
    new_soup = BeautifulSoup(new_html, "html.parser")

    old_cells = old_soup.find_all("td")
    new_cells = new_soup.find_all("td")

    for i in range(min(len(old_cells), len(new_cells))):
        old_text = old_cells[i].get_text(strip=True)
        new_text = new_cells[i].get_text(strip=True)

        if old_text != new_text:
            old_cells[i].string = ""
            new_cells[i].string = ""

            old_cells[i].append(
                BeautifulSoup(
                    f"<span class='bg-red-200 px-1 rounded'>{old_text}</span>",
                    "html.parser"
                )
            )

            new_cells[i].append(
                BeautifulSoup(
                    f"<span class='bg-green-200 px-1 rounded'>{new_text}</span>",
                    "html.parser"
                )
            )

    return str(old_soup), str(new_soup)

def highlight_words(old, new):
    result_old = []
    result_new = []

    diff = difflib.ndiff(old.split(), new.split())

    for d in diff:
        if d.startswith("- "):
            result_old.append(f"<span class='bg-red-200'>{d[2:]}</span>")
        elif d.startswith("+ "):
            result_new.append(f"<span class='bg-green-200'>{d[2:]}</span>")
        elif d.startswith("  "):
            word = d[2:]
            result_old.append(word)
            result_new.append(word)

    return " ".join(result_old), " ".join(result_new)


@router.get("/ui/{repo}/{api}/diff", response_class=HTMLResponse)
def api_diff(request: Request, repo: str, api: str, v1: int, v2: int):

    md1 = docs_store.read_version(repo, api, v1)
    md2 = docs_store.read_version(repo, api, v2)

    if md1 is None or md2 is None:
        return HTMLResponse("Invalid versions", status_code=404)

    # Generate LLM summary of changes
    summary = summarize_changes(md1, md2, api)

    content1 = html_to_blocks(render_md(md1))
    content2 = html_to_blocks(render_md(md2))

    diff_rows = generate_diff(content1, content2)

    return templates.TemplateResponse(
        request,
        "diff.html",
        {
            "repo": repo,
            "api": api,
            "v1": v1,
            "v2": v2,
            "rows": diff_rows,
            "summary": summary
        }
    )


def _render_doc_page(request, repo, api, version, md_content):
    md = markdown.Markdown(extensions=DOC_MARKDOWN_EXTENSIONS)

    html_content = md.convert(md_content)
    toc = getattr(md, 'toc', '')

    return templates.TemplateResponse(
        request,
        "view.html",
        {
            "repo": repo,
            "api": api,
            "version": version,
            "content": html_content,
            "toc": toc,
            **_nav_context(repo)
        }
    )


@router.get("/ui/{repo}/{api}/view/{version}", response_class=HTMLResponse)
def view_doc(request: Request, repo: str, api: str, version: str):

    md_content = docs_store.read_version(repo, api, version)

    if md_content is None:
        return HTMLResponse("Document not found", status_code=404)

    return _render_doc_page(request, repo, api, version, md_content)


@router.get("/ui/{repo}/{api}", response_class=HTMLResponse)
def view_latest(request: Request, repo: str, api: str):

    if not docs_store.api_path(repo, api):
        return HTMLResponse("API not found", status_code=404)

    versions = docs_store.list_versions(repo, api)

    if not versions:
        return HTMLResponse("No versions found", status_code=404)

    latest_version = versions[-1]
    md_content = docs_store.read_version(repo, api, latest_version)

    return _render_doc_page(request, repo, api, latest_version, md_content)


@router.get("/ui/{repo}/{api}/history", response_class=HTMLResponse)
def api_versions(request: Request, repo: str, api: str):

    if not docs_store.api_path(repo, api):
        return HTMLResponse("API not found", status_code=404)

    # Oldest first; markdown files carry no commit hash
    versions = [
        {"version": v, "commit_hash": "N/A"}
        for v in docs_store.list_versions(repo, api)
    ]

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "repo": repo,
            "api": api,
            "versions": versions,
            **_nav_context(repo)
        }
    )


@router.get("/ui/search", response_class=HTMLResponse)
def ui_search(request: Request):
    """Display the LLM search page"""
    return templates.TemplateResponse(request, "llm_search.html", {})


@router.get("/ui/{repo}", response_class=HTMLResponse)
def repo_home(request: Request, repo: str):

    if not docs_store.is_safe_name(repo) or repo not in docs_store.list_repos():
        return HTMLResponse(f"Repository '{repo}' not found", status_code=404)

    apis = docs_store.list_apis(repo)

    if not apis:
        return HTMLResponse(f"No APIs found in repository '{repo}'", status_code=404)

    # Redirect to the first API alphabetically
    return RedirectResponse(url=f"/ui/{quote(repo, safe='')}/{quote(apis[0], safe='')}")


@router.post("/api/search")
async def search_apis(request: Request):
    """
    Search APIs and answer user questions based on available documentation.
    Acts as a conversational KT provider/assistant.
    
    Request body:
    {
        "query": "user string query about APIs"
    }
    
    Returns:
    {
        "success": true/false,
        "answer": "Conversational answer based on docs or error message",
        "results": [{"repo": "...", "api": "...", "version": 1}],
        "matchedCount": number of matching APIs
    }
    """
    try:
        body = await request.json()
        query = body.get("query", "").strip()
        
        if not query:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Query cannot be empty",
                    "answer": "Please enter a question or search term.",
                    "results": [],
                    "matchedCount": 0
                },
                status_code=400
            )
        
        # LLM calls block, keep them off the event loop
        matching_apis = await run_in_threadpool(search_apis_rag, query)
        
        # Generate a conversational answer based on actual documentation
        answer_result = await run_in_threadpool(answer_question_based_on_docs, query, matching_apis)
        
        return JSONResponse({
            "success": answer_result["success"],
            "answer": answer_result["answer"] or answer_result["message"],
            "message": answer_result["message"],
            "results": matching_apis,
            "matchedCount": len(matching_apis)
        })
        
    except Exception as e:
        print(f"Error in search_apis: {e}")
        return JSONResponse(
            {
                "success": False,
                "error": str(e),
                "answer": "Something went wrong on our end. Please try again later.",
                "results": [],
                "matchedCount": 0
            },
            status_code=500
        )


@router.get("/ui/{repo}/{api}/qa-plan", response_class=HTMLResponse)
def qa_plan_view(request: Request, repo: str, api: str, v1: int = None, v2: int = None, force: bool = False):
    """
    Generate and display a comprehensive QA plan for an API.
    QA plans are cached to avoid regenerating on every page load.
    If v1 and v2 are provided, includes regression testing.
    
    Args:
        force: If True, bypass cache and regenerate QA plan
    """
    if not docs_store.api_path(repo, api):
        return HTMLResponse(f"API '{api}' not found in repository '{repo}'", status_code=404)

    versions = docs_store.list_versions(repo, api)

    if not versions:
        return HTMLResponse("No versions found for this API", status_code=404)

    v1, v2 = _default_versions(versions, v1, v2)

    # Check if QA plan is cached (unless force=True to bypass cache)
    cached_plan = None if force else qa_plan_service.get_qa_plan(repo, api, v2)
    
    if cached_plan:
        qa_plan = cached_plan.get("plan", {})
        plan_generated_at = cached_plan.get("generated_at")
    else:
        doc_v2 = docs_store.read_version(repo, api, v2)

        if doc_v2 is None:
            return HTMLResponse(f"Version v{v2} not found", status_code=404)

        # v1 doc enables regression testing
        doc_v1 = docs_store.read_version(repo, api, v1) if v1 else None

        qa_plan = generate_full_qa_plan(api, doc_v2, doc_v1)
        qa_plan_service.save_qa_plan(repo, api, v2, qa_plan)

        plan_generated_at = datetime.utcnow().isoformat()

    return templates.TemplateResponse(
        request,
        "qa_plan.html",
        {
            "repo": repo,
            "api": api,
            "v1": v1,
            "v2": v2,
            "qa_plan": qa_plan,
            "plan_generated_at": plan_generated_at,
            "is_cached": cached_plan is not None,
            **_nav_context(repo)
        }
    )


@router.get("/api/qa-plan")
def generate_qa_plan_api(repo: str, api: str, v1: int = None, v2: int = None, force: bool = False):
    """
    API endpoint to generate QA plan as JSON.
    Useful for integrating with CI/CD pipelines.
    
    Args:
        force: If True, bypass cache and regenerate QA plan
    """
    if not docs_store.api_path(repo, api):
        return JSONResponse(
            {"error": f"API '{api}' not found in repository '{repo}'"},
            status_code=404
        )

    versions = docs_store.list_versions(repo, api)

    if not versions:
        return JSONResponse(
            {"error": "No versions found for this API"},
            status_code=404
        )

    v1, v2 = _default_versions(versions, v1, v2)

    if v2 not in versions:
        return JSONResponse(
            {"error": f"Version v{v2} not found"},
            status_code=404
        )

    # Check cache (unless force=True)
    cached_plan = None if force else qa_plan_service.get_qa_plan(repo, api, v2)
    
    if cached_plan:
        qa_plan = cached_plan.get("plan", {})
    else:
        doc_v2 = docs_store.read_version(repo, api, v2)
        doc_v1 = docs_store.read_version(repo, api, v1) if v1 else None

        qa_plan = generate_full_qa_plan(api, doc_v2, doc_v1)
        qa_plan_service.save_qa_plan(repo, api, v2, qa_plan)
    
    return JSONResponse(qa_plan)


# ============================================================================
# DEPENDENCY ANALYSIS ENDPOINTS
# ============================================================================

@router.get("/api/dependency-graph")
def get_dependency_graph_api(repo: str):
    """
    Get dependency graph for a repository showing API relationships.
    
    Query params:
        repo: Repository name
    """
    if not docs_store.is_safe_name(repo):
        return JSONResponse({"nodes": [], "links": []})

    graph = build_dependency_graph(repo)
    return JSONResponse(graph)


@router.get("/api/impact-analysis")
def get_impact_analysis_api(repo: str, api: str, v1: int, v2: int):
    """
    Get breaking change impact analysis for an API upgrade.
    
    Query params:
        repo: Repository name
        api: API name
        v1: Version 1
        v2: Version 2
    """
    v1_content = docs_store.read_version(repo, api, v1) or ""
    v2_content = docs_store.read_version(repo, api, v2) or ""

    impact = get_impact_analysis(repo, api, v1_content, v2_content)
    return JSONResponse(impact)


# ============================================================================
# TEST TEMPLATES ENDPOINTS
# ============================================================================

@router.get("/api/templates/predefined")
def get_predefined_templates_api():
    """Get all predefined test templates."""
    return JSONResponse(get_predefined_templates())


@router.get("/api/templates/recommendations")
def get_template_recommendations_api(api: str):
    """Get recommended templates for an API."""
    return JSONResponse(get_template_recommendations(api))


@router.get("/api/templates/list")
def list_templates_api(repo: str, category: str = None):
    """
    List custom templates for a repository.
    
    Query params:
        repo: Repository name
        category: Optional category filter
    """
    return JSONResponse(list_templates(repo, category))


@router.post("/api/templates/create")
def create_template_api(
    repo: str,
    name: str,
    category: str,
    description: str,
    test_cases: List[str]
):
    """
    Create a new custom test template.
    
    Body params:
        repo: Repository name
        name: Template name
        category: Test category
        description: Template description
        test_cases: List of test case descriptions
    """
    success = create_template(repo, name, category, description, test_cases)
    
    return JSONResponse({
        "success": success,
        "message": f"Template '{name}' created successfully" if success else "Failed to create template"
    })


@router.get("/ui/{repo}/{api}/dependencies", response_class=HTMLResponse)
def dependencies_view(request: Request, repo: str, api: str, v1: int = None, v2: int = None):
    """
    Display dependency analysis and impact graph for API changes.
    """
    if not docs_store.api_path(repo, api):
        return HTMLResponse(f"API '{api}' not found in repository '{repo}'", status_code=404)

    versions = docs_store.list_versions(repo, api)

    if not versions:
        return HTMLResponse("No versions found for this API", status_code=404)

    v1, v2 = _default_versions(versions, v1, v2)

    v2_content = docs_store.read_version(repo, api, v2)

    if v2_content is None:
        return HTMLResponse(f"Version v{v2} not found", status_code=404)

    v1_content = (docs_store.read_version(repo, api, v1) if v1 else None) or ""

    impact_data = get_impact_analysis(repo, api, v1_content, v2_content)

    return templates.TemplateResponse(
        request,
        "dependencies.html",
        {
            "repo": repo,
            "api": api,
            "v1": v1,
            "v2": v2,
            "impact_data": impact_data,
            **_nav_context(repo)
        }
    )


@router.get("/ui/{repo}/{api}/templates", response_class=HTMLResponse)
def templates_view(request: Request, repo: str, api: str):
    """
    Display and manage test templates for an API.
    """
    if not docs_store.api_path(repo, api):
        return HTMLResponse(f"API '{api}' not found in repository '{repo}'", status_code=404)

    return templates.TemplateResponse(
        request,
        "templates.html",
        {
            "repo": repo,
            "api": api,
            "recommendations": get_template_recommendations(api),
            "custom_templates": list_templates(repo),
            "predefined_templates": get_predefined_templates(),
            **_nav_context(repo)
        }
    )
