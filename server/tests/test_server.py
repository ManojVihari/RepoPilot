import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schema import Route
from app.services import docs_store, test_templates
from app.services.markdown_builder import MarkdownBuilder
from app.services.signature_service import SignatureService
import app.api.routes as routes


FASTAPI_ROUTE = {
    "function": "get_item",
    "method": "GET",
    "path": "/items/{item_id}",
    "params": [{"name": "item_id", "type": "int", "required": True, "default": None}],
    "errors": [404],
    "calls": ["load_item(item_id)"],
    "call_graph": {"get_item": ["load_item"]},
}

SPRING_ROUTE = {
    "function": "getUser",
    "method": "GET",
    "path": "/users/{id}",
    "params": [{"name": "id", "type": "Long", "in": "path", "required": True, "default": None}],
    "errors": ["NOT_FOUND", {"type": "VALIDATION_ERROR", "status": 400, "fields": [{"name": "email", "rule": "max"}]}],
    "response": {"type": "UserDto", "schema": {"id": {"type": "Long"}}},
    "db_ops": [],
}


@pytest.fixture
def docs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_store, "DOCS_DIR", str(tmp_path))

    api = tmp_path / "shop" / "get_item"
    api.mkdir(parents=True)
    (api / "v1.md").write_text("# API: get_item\n\nfirst")
    (api / "v2.md").write_text("# API: get_item\n\nsecond")
    (api / "vdraft.md").write_text("ignored")
    (tmp_path / "shop" / "empty_api").mkdir()
    (tmp_path / ".hidden").mkdir()

    return tmp_path


@pytest.fixture
def client():
    return TestClient(app)


def test_analyze_accepts_scanner_error_formats(client, monkeypatch):
    received = []
    monkeypatch.setattr(routes, "process_routes", lambda *args: received.append(args))

    payload = {
        "scanner_version": "1.1",
        "repository": "shop",
        "commit": "abc123",
        "frameworks": ["fastapi", "spring"],
        "routes": [FASTAPI_ROUTE, SPRING_ROUTE],
    }

    response = client.post("/analyze", json=payload)

    assert response.status_code == 200
    assert len(received) == 1


def test_signature_tracks_params_and_errors():
    signature = SignatureService()
    base = signature.generate(Route(**FASTAPI_ROUTE))

    assert signature.generate(Route(**FASTAPI_ROUTE)) == base

    more_params = dict(FASTAPI_ROUTE, params=FASTAPI_ROUTE["params"] + [{"name": "q", "type": "str"}])
    assert signature.generate(Route(**more_params)) != base

    other_errors = dict(FASTAPI_ROUTE, errors=[404, 410])
    assert signature.generate(Route(**other_errors)) != base


def test_signature_ignores_ordering():
    signature = SignatureService()
    route = dict(SPRING_ROUTE, errors=list(reversed(SPRING_ROUTE["errors"])))

    assert signature.generate(Route(**route)) == signature.generate(Route(**SPRING_ROUTE))


def test_markdown_lists_scalar_and_validation_errors():
    md = MarkdownBuilder().build(Route(**SPRING_ROUTE), {})

    assert "| NOT_FOUND | - | - |" in md
    assert "| 400 | email | max |" in md


def test_docs_store_listing(docs_dir):
    assert docs_store.list_repos() == ["shop"]
    assert docs_store.list_apis("shop") == ["get_item"]
    assert docs_store.list_versions("shop", "get_item") == [1, 2]
    assert docs_store.read_version("shop", "get_item", 2).endswith("second")
    assert docs_store.read_version("shop", "get_item", 9) is None


@pytest.mark.parametrize("repo, api", [("..", "shop"), ("shop", ".."), ("", "x"), ("a/b", "c")])
def test_docs_store_rejects_path_escape(docs_dir, repo, api):
    assert docs_store.api_path(repo, api) is None
    assert docs_store.read_version(repo, api, 1) is None


def test_ui_pages_render(docs_dir, client):
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/shop/get_item").status_code == 200
    assert client.get("/ui/shop/get_item/history").status_code == 200

    dashboard = client.get("/ui/shop")
    assert dashboard.status_code == 200
    assert 'href="/ui/shop/get_item"' in dashboard.text

    assert client.get("/ui/shop/missing").status_code == 404


def test_template_names_cannot_escape_templates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(test_templates, "TEMPLATES_DIR", str(tmp_path / "templates"))

    assert test_templates.create_template("shop", "../../evil", "x", "y", []) is False
    assert test_templates.create_template("..", "ok", "x", "y", []) is False
    assert test_templates.create_template("shop", "Happy Path", "x", "y", ["a"]) is True
    assert test_templates.get_template("shop", "Happy Path")["test_count"] == 1


# ============================
# SPRING SCANNER PAYLOAD (scanner 2.0)
# ============================

import json
from pathlib import Path

from app.services import architecture_store, doc_service

SPRING_SCAN = Path(__file__).parent / "fixtures" / "spring_shop_scan.json"


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_store, "DOCS_DIR", str(tmp_path / "docs"))
    monkeypatch.setattr(doc_service.markdown_writer, "base_path", str(tmp_path / "docs"))
    monkeypatch.setattr(doc_service.version_service, "base_path", str(tmp_path / "database"))
    monkeypatch.setattr(architecture_store, "ARCHITECTURE_DIR", str(tmp_path / "architecture"))
    # no LLM in tests: the generator falls back to its template output
    monkeypatch.setattr(doc_service.generator, "ollama_url", "http://127.0.0.1:9/unreachable")
    return tmp_path


def test_spring_scan_is_documented_and_architecture_stored(client, isolated_storage):
    payload = json.loads(SPRING_SCAN.read_text())

    assert client.post("/analyze", json=payload).status_code == 200

    doc = docs_store.read_version("spring-shop", "OrderController.create", 1)
    assert "| 402 | PAYMENT_REQUIRED | PaymentDeclinedException (ApiErrors.declined) |" in doc
    assert "**Messaging** (kafka): publish to `orders.created.v1` (OrderCreatedEvent)" in doc
    assert "**HTTP call** (rest_template): POST `https://api.payments.example.com/v1/charges/{id}`" in doc
    assert "- **Handler:** `OrderController.create`" in doc
    assert "URL rule `/**` → **authenticated**" in doc

    stored = client.get("/api/architecture", params={"repo": "spring-shop"}).json()
    assert stored["commit"] == "abc1234"
    assert stored["architecture"]["spring"]["summary"]["endpoints"] == 4

    by_commit = client.get("/api/architecture", params={"repo": "spring-shop", "commit": "abc1234"})
    assert by_commit.status_code == 200
    assert client.get("/api/architecture", params={"repo": "../etc"}).status_code == 404


def test_status_code_source_does_not_change_signature():
    route = json.loads(SPRING_SCAN.read_text())["routes"][0]
    moved = dict(route, status_codes=[dict(s, source="somewhere else") for s in route["status_codes"]])

    signature = SignatureService()
    assert signature.generate(Route(**route)) == signature.generate(Route(**moved))

    fewer = dict(route, status_codes=route["status_codes"][:1])
    assert signature.generate(Route(**route)) != signature.generate(Route(**fewer))


# ============================
# ARCHITECTURE PAGE & SCANNER-BASED IMPACT ANALYSIS
# ============================

from app.services import architecture_view
from app.services.dependency_analyzer import contract_changes

SPRING_SCAN_V2 = Path(__file__).parent / "fixtures" / "spring_shop_scan_v2.json"


@pytest.fixture
def two_versions(client, isolated_storage, monkeypatch):
    """spring-shop scanned at two commits (DTO, response and payment URL changed)."""
    monkeypatch.setattr(routes.version_service, "base_path", str(isolated_storage / "database"))
    for scan in (SPRING_SCAN, SPRING_SCAN_V2):
        assert client.post("/analyze", json=json.loads(scan.read_text())).status_code == 200
    return client


def test_versions_store_structured_route_data(two_versions):
    assert docs_store.list_versions("spring-shop", "OrderController.create") == [1, 2]
    # response shape changed -> get/list got a new version too; cancel did not change
    assert docs_store.list_versions("spring-shop", "OrderController.get") == [1, 2]
    assert docs_store.list_versions("spring-shop", "OrderController.cancel") == [1]

    route = routes.version_service.get_route("spring-shop", "OrderController.create", 2)
    assert route["handler"] == "OrderController.create"
    assert route["integrations"]["external_apis"][0]["url"].endswith("/v2/charges/{id}")


def test_impact_analysis_compares_versions(two_versions):
    impact = two_versions.get("/api/impact-analysis", params={"repo": "spring-shop", "api": "OrderController.create", "v1": 1, "v2": 2}).json()

    assert impact["source"] == "scanner"
    changes = [(c["severity"], c["type"], c["detail"]) for c in impact["breaking_changes"]["changes"]]
    assert changes == [
        ("breaking", "VALIDATION_CHANGED", "`lines.quantity` minimum: 1 → 5"),
        ("breaking", "REQUEST_FIELD_ADDED", "`lines.warehouse` (String) is required"),
        ("breaking", "RESPONSE_FIELD_TYPE_CHANGED", "`status`: String → OrderStatus"),
        ("additive", "RESPONSE_FIELD_ADDED", "`trackingUrl` (String)"),
    ]
    assert impact["breaking_changes"]["impact_level"] == "high"

    external = impact["downstream_changes"]["external_apis"]
    assert external["added"][0].startswith("POST https://api.payments.example.com/v2/charges/{id}")
    assert external["removed"][0].startswith("POST https://api.payments.example.com/v1/charges/{id}")

    shared = {(s["kind"], s["name"]) for s in impact["shared_resources"]}
    assert {("table", "orders"), ("cache", "orders"), ("topic", "orders.created.v1")} <= shared

    graph_nodes = {n["id"] for n in impact["dependency_graph"]["nodes"]}
    assert {"table:orders", "topic:orders.created.v1", "service:api.payments.example.com"} <= graph_nodes

    missing = two_versions.get("/api/impact-analysis", params={"repo": "spring-shop", "api": "OrderController.cancel", "v1": 1, "v2": 2})
    assert missing.status_code == 404


def test_architecture_and_dependency_pages_render(two_versions):
    page = two_versions.get("/ui/spring-shop/architecture")
    assert page.status_code == 200
    assert "spring-shop architecture" in page.text
    assert "orders.created.v1" in page.text
    assert "api.payments.example.com" in page.text

    deps = two_versions.get("/ui/spring-shop/OrderController.create/dependencies", params={"v1": 1, "v2": 2})
    assert deps.status_code == 200
    assert "Based on scanner data" in deps.text
    assert "REQUEST_FIELD_ADDED" in deps.text

    assert 'href="/ui/spring-shop/architecture"' in two_versions.get("/ui/spring-shop/OrderController.create").text
    assert two_versions.get("/ui/unknown-repo/architecture").status_code == 404


def test_dependency_graph_api_views(two_versions):
    system = two_versions.get("/api/dependency-graph", params={"repo": "spring-shop"}).json()
    assert system["source"] == "scanner"
    assert all(n["type"] != "component" for n in system["nodes"])
    links = {(l["source"], l["target"], l["kind"]) for l in system["links"]}
    assert ("module:", "broker:kafka", "publishes") in links
    assert ("module:", "external:api.payments.example.com", "http") in links

    components = two_versions.get("/api/dependency-graph", params={"repo": "spring-shop", "view": "components", "module": ""}).json()
    kinds = {l["kind"] for l in components["links"]}
    assert {"injects", "calls", "write", "publishes", "http"} <= kinds


def test_contract_changes_for_params_and_security():
    old = {"method": "GET", "path": "/items", "params": [
        {"name": "q", "in": "query", "type": "String", "required": False},
        {"name": "id", "in": "path", "type": "Long", "required": True},
    ], "status_codes": [{"code": 200}], "security": {"annotations": []}}
    new = {"method": "GET", "path": "/items", "params": [
        {"name": "q", "in": "query", "type": "String", "required": True},
        {"name": "tenant", "in": "header", "type": "String", "required": True},
    ], "status_codes": [{"code": 200}, {"code": 404}],
        "security": {"annotations": [{"annotation": "PreAuthorize", "expression": "hasRole('ADMIN')"}]}}

    found = {(c["type"], c["severity"]) for c in contract_changes(old, new)}
    assert found == {
        ("PARAM_NOW_REQUIRED", "breaking"),
        ("PARAM_ADDED", "breaking"),
        ("PARAM_REMOVED", "breaking"),      # path parameter
        ("ERROR_STATUS_ADDED", "minor"),
        ("SECURITY_CHANGED", "breaking"),
    }


def test_consumers_from_feign_calls_and_gateway_routes():
    model = {
        "modules": [
            {"path": "stats", "name": "stats", "runtime": {"application_name": "statistics-service", "context_path": "/statistics"}},
            {"path": "account", "name": "account", "runtime": {"application_name": "account-service"}},
            {"path": "gateway", "name": "gateway", "runtime": {"application_name": "gateway"}},
        ],
        "endpoints": [{"method": "PUT", "path": "/{accountName}", "handler": "StatisticsController.save", "module": "stats"}],
        "graph": {
            "nodes": [
                {"id": "module:stats", "type": "module", "name": "statistics-service", "path": "stats"},
                {"id": "module:gateway", "type": "module", "name": "gateway", "path": "gateway"},
                {"id": "module:account", "type": "module", "name": "account-service", "path": "account"},
                {"id": "component:a.AccountServiceImpl", "type": "component", "name": "AccountServiceImpl", "module": "account"},
            ],
            "edges": [
                {"from": "component:a.AccountServiceImpl", "to": "module:stats", "kind": "http", "details": ["PUT /statistics/{accountName}"]},
                {"from": "module:account", "to": "module:stats", "kind": "http", "details": ["PUT /statistics/{accountName}"]},
                {"from": "module:account", "to": "module:stats", "kind": "http", "details": ["GET /statistics/current"]},
                {"from": "module:gateway", "to": "module:stats", "kind": "routes", "details": ["/statistics/**"]},
            ],
        },
    }

    found = architecture_view.consumers(model, model["endpoints"][0])
    assert [(c["kind"], c["caller"], c["caller_module"]) for c in found] == [
        ("service_call", "AccountServiceImpl", "account-service"),
        ("gateway_route", "gateway", "gateway"),
    ]


# ============================
# DISPLAY TITLES
# ============================

from app.services.doc_generator import fallback_title


def _llm_returning(titles):
    """Fake generator: returns the given titles in order, like the LLM would."""
    queue = list(titles)

    def generate(route):
        title = queue.pop(0)
        return json.dumps({"title": title, "title_source": "llm" if title else "fallback",
                           "overview": "o", "business_flow": [], "business_logic": "", "change_impact": ""})
    return generate


def test_title_is_display_only_and_stable(client, isolated_storage, monkeypatch):
    monkeypatch.setattr(routes.version_service, "base_path", str(isolated_storage / "database"))
    # v1 and v2 of create get different LLM wording; the first one is kept
    monkeypatch.setattr(doc_service.generator, "generate_explanation",
                        _llm_returning(["Place Order", "Search Order", "Cancel Order", "List Orders", "Create a New Order", "Find Order", "List All Orders"]))

    for scan in (SPRING_SCAN, SPRING_SCAN_V2):
        assert client.post("/analyze", json=json.loads(scan.read_text())).status_code == 200

    api = "OrderController.create"
    # versions still tracked under the stable name
    assert docs_store.list_versions("spring-shop", api) == [1, 2]
    assert docs_store.get_title("spring-shop", api) == "Place Order"
    assert docs_store.read_version("spring-shop", api, 2).startswith("# Place Order\n")
    assert routes.version_service.get_version("spring-shop", api, 2)["title"] == "Place Order"

    page = client.get(f"/ui/spring-shop/{api}").text
    assert "Place Order" in page and api in page          # title shown, id kept visible
    assert f'href="/ui/spring-shop/{api}"' in page        # links use the stable name


def test_fallback_title_is_upgraded_by_llm(isolated_storage):
    from app.services.doc_service import _stable_title

    assert _stable_title("r", "A.b", {"title": "B", "title_source": "fallback"}) == "B"
    assert _stable_title("r", "A.b", {"title": "Book Room", "title_source": "llm"}) == "Book Room"
    assert _stable_title("r", "A.b", {"title": "Reserve Room", "title_source": "llm"}) == "Book Room"
    assert _stable_title("r", "A.b", {"title": "B", "title_source": "fallback"}) == "Book Room"


def test_fallback_title_sources():
    assert fallback_title(Route(method="GET", path="/x", function="processFindForm")) == "Process Find Form"
    assert fallback_title(Route(method="GET", path="/x", function="initCreationForm", handler="PetController.initCreationForm")) == "Init Creation Form (Pet)"
    assert fallback_title(Route(method="GET", path="/x", function="showOwner", handler="OwnerController.showOwner")) == "Show Owner"
    assert fallback_title(Route(method="GET", path="/x", function="f", summary="Find owners")) == "Find owners"
    assert fallback_title(Route(method="GET", path="/x", function="f", description="returns the vets. Cached.")) == "Returns the vets"


def test_llm_title_is_parsed_and_cleaned(monkeypatch):
    from app.services import doc_generator

    class FakeResponse:
        def json(self):
            return {"response": 'Sure! {"title": "\\"Search Owners by Last Name.\\"", "overview": "o", "business_flow": ["a"]}'}

    monkeypatch.setattr(doc_generator, "LLM_ENABLED", True)
    monkeypatch.setattr(doc_generator.requests, "post", lambda *a, **k: FakeResponse())

    result = json.loads(doc_generator.APIDocGenerator().generate_explanation(Route(**FASTAPI_ROUTE)))
    assert (result["title"], result["title_source"]) == ("Search Owners by Last Name", "llm")

    class BadTitle(FakeResponse):
        def json(self):
            return {"response": '{"title": "GET /owners/{id}", "overview": "o", "business_flow": []}'}

    monkeypatch.setattr(doc_generator.requests, "post", lambda *a, **k: BadTitle())
    result = json.loads(doc_generator.APIDocGenerator().generate_explanation(Route(**FASTAPI_ROUTE)))
    assert (result["title"], result["title_source"]) == ("Get Item", "fallback")


# ============================
# UI SHELL: home, dashboards, navigation, accessibility
# ============================

UI_PAGES = [
    "/", "/ui", "/ui/all", "/ui/search", "/ui/spring-shop", "/ui/spring-shop/architecture",
    "/ui/spring-shop/OrderController.create", "/ui/spring-shop/OrderController.create/history",
    "/ui/spring-shop/OrderController.create/diff?v1=1&v2=2",
    "/ui/spring-shop/OrderController.create/dependencies?v1=1&v2=2",
    "/ui/spring-shop/OrderController.create/qa-plan", "/ui/spring-shop/OrderController.create/templates",
]


def test_every_page_uses_the_accessible_shell(two_versions, monkeypatch):
    from app.api import routes as r
    monkeypatch.setattr(r, "summarize_changes", lambda *a: None)
    monkeypatch.setattr(r, "generate_full_qa_plan", lambda *a: {"coverage": {}, "test_cases": {}, "is_template": True})
    monkeypatch.setattr(r.qa_plan_service, "save_qa_plan", lambda *a, **k: True)

    for url in UI_PAGES:
        page = two_versions.get(url)
        assert page.status_code == 200, url
        html = page.text
        assert '<html lang="en">' in html, url
        assert 'class="skip-link" href="#main"' in html, url
        assert 'id="main"' in html, url
        assert '<nav class="mainnav" aria-label="Main">' in html, url
        assert "cdn.tailwindcss.com" not in html and "font-awesome" not in html, url


def test_api_pages_mark_the_current_tab(two_versions):
    api = "/ui/spring-shop/OrderController.create"
    assert f'<a href="{api}/history" aria-current="page">' in two_versions.get(api + "/history").text
    docs = two_versions.get(api).text
    assert f'<a href="{api}" aria-current="page">' in docs
    assert "Changes from v1" in docs   # quick link to the diff of the latest version


def test_home_lists_repositories_and_recent_changes(two_versions):
    home = two_versions.get("/").text
    assert 'href="/ui/spring-shop"' in home
    assert "Recently documented" in home
    assert 'href="/ui/spring-shop/OrderController.create/diff?v1=1&v2=2"' in home


def test_history_shows_commits(two_versions):
    history = two_versions.get("/ui/spring-shop/OrderController.create/history").text
    assert "def5678" in history and "abc1234" in history
    assert "N/A" not in history


def test_unknown_pages_render_a_friendly_404(client, docs_dir):
    for url in ("/ui/nope", "/ui/shop/nope", "/ui/shop/get_item/view/99", "/ui/nope/architecture"):
        page = client.get(url)
        assert page.status_code == 404, url
        assert "Go back" in page.text, url
