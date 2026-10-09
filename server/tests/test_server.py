import html
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.schema import Route
from app.services import docs_store, test_templates
from app.services.markdown_builder import MarkdownBuilder
from app.services.signature_service import SignatureService
from app import auth, jobs


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
def docs_dir():
    """shop/get_item documented twice (the name is kept from the file-based layout)."""
    docs_store.save_version_if_changed("shop", "get_item", "sig-1", "c1", "# API: get_item\n\nfirst")
    docs_store.save_version_if_changed("shop", "get_item", "sig-2", "c2", "# API: get_item\n\nsecond")


ADMIN_EMAIL, ADMIN_PASSWORD = "admin@example.com", "admin-password-1"


def signed_in_client(email, password, role="admin"):
    """A browser signed in through the real sign-in form, with an API key of the same user."""
    user = auth.create_user(email, email.split("@")[0], role, password)
    browser = TestClient(app)
    response = browser.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert response.status_code == 303, response.text
    browser.api_key = auth.create_api_key(user["id"], "tests")["key"]
    browser.user = user
    return browser


@pytest.fixture
def client():
    return signed_in_client(ADMIN_EMAIL, ADMIN_PASSWORD)


def bearer(client):
    return {"Authorization": f"Bearer {client.api_key}"}


def analyze(client, payload, **kwargs):
    """Upload a scan (as the scanner does, with an API key) and run the job it queues, as a worker would."""
    response = client.post("/analyze", json=payload, headers=bearer(client), **kwargs)
    assert response.status_code == 202, response.text
    jobs.run_pending()
    return response


def test_analyze_accepts_scanner_error_formats(client, monkeypatch):
    received = []
    monkeypatch.setattr(doc_service, "process_routes", lambda *args, **kw: received.append(args) or {})

    payload = {
        "scanner_version": "1.1",
        "repository": "shop",
        "commit": "abc123",
        "frameworks": ["fastapi", "spring"],
        "routes": [FASTAPI_ROUTE, SPRING_ROUTE],
    }

    response = analyze(client, payload)

    assert response.json()["status"] == "queued"
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
    assert not docs_store.api_exists(repo, api)
    assert docs_store.read_version(repo, api, 1) is None


def test_ui_pages_render(docs_dir, client):
    assert client.get("/ui").status_code == 200
    assert client.get("/ui/shop/get_item").status_code == 200
    assert client.get("/ui/shop/get_item/history").status_code == 200

    dashboard = client.get("/ui/shop")
    assert dashboard.status_code == 200
    assert 'href="/ui/shop/get_item"' in dashboard.text

    assert client.get("/ui/shop/missing").status_code == 404


def test_template_names_are_validated_and_stored_per_repo():
    assert test_templates.create_template("shop", "../../evil", "x", "y", []) is False
    assert test_templates.create_template("..", "ok", "x", "y", []) is False
    assert test_templates.create_template("shop", "Happy Path", "x", "y", ["a"]) is True
    assert test_templates.get_template("shop", "Happy Path")["test_count"] == 1
    assert test_templates.create_template("shop", "happy-path", "x", "y", ["a", "b"]) is True    # same key: replaced
    assert [t["test_count"] for t in test_templates.list_templates("shop")] == [2]
    assert test_templates.list_templates("other") == []
    assert test_templates.delete_template("shop", "Happy Path") is True
    assert test_templates.get_template("shop", "Happy Path") is None


# ============================
# SPRING SCANNER PAYLOAD (scanner 2.0)
# ============================

import json
from pathlib import Path

from app.services import doc_service

SPRING_SCAN = Path(__file__).parent / "fixtures" / "spring_shop_scan.json"


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    # no LLM in tests: the generator falls back to its template output
    monkeypatch.setattr(doc_service.generator, "ollama_url", "http://127.0.0.1:9/unreachable")
    return tmp_path


def test_spring_scan_is_documented_and_architecture_stored(client, isolated_storage):
    payload = json.loads(SPRING_SCAN.read_text())

    analyze(client, payload)

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
    for scan in (SPRING_SCAN, SPRING_SCAN_V2):
        analyze(client, json.loads(scan.read_text()))
    return client


def test_versions_store_structured_route_data(two_versions):
    assert docs_store.list_versions("spring-shop", "OrderController.create") == [1, 2]
    # response shape changed -> get/list got a new version too; cancel did not change
    assert docs_store.list_versions("spring-shop", "OrderController.get") == [1, 2]
    assert docs_store.list_versions("spring-shop", "OrderController.cancel") == [1]

    route = docs_store.get_route("spring-shop", "OrderController.create", 2)
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
    assert "Based on the scanned source code" in deps.text
    assert "Hold before merging" in deps.text
    assert "Request body" in deps.text          # change area instead of the raw type code
    assert "REQUEST_FIELD_ADDED" not in deps.text.split("<script")[0]
    assert "What this API uses" in deps.text

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
    # v1 and v2 of create get different LLM wording; the first one is kept
    monkeypatch.setattr(doc_service.generator, "generate_explanation",
                        _llm_returning(["Place Order", "Search Order", "Cancel Order", "List Orders", "Create a New Order", "Find Order", "List All Orders"]))

    for scan in (SPRING_SCAN, SPRING_SCAN_V2):
        analyze(client, json.loads(scan.read_text()))

    api = "OrderController.create"
    # versions still tracked under the stable name
    assert docs_store.list_versions("spring-shop", api) == [1, 2]
    assert docs_store.get_title("spring-shop", api) == "Place Order"
    assert docs_store.read_version("spring-shop", api, 2).startswith("# Place Order\n")
    assert docs_store.version_entry("spring-shop", api, 2)["title"] == "Place Order"

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


def test_landing_page_describes_the_product(two_versions):
    page = two_versions.get("/").text
    assert "Know what a change breaks before you merge it" in page
    assert 'href="/ui"' in page and 'id="how"' in page
    assert "Documenting 4 APIs across 1 repository" in page


def test_home_lists_repositories_and_recent_changes(two_versions):
    home = two_versions.get("/ui").text
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


def test_qa_plan_shows_progress_then_the_generated_plan(two_versions, monkeypatch):
    from app.api import routes as r
    calls = []
    plan = {"coverage": {"overall_score": 81}, "test_cases": {}, "is_template": True}
    monkeypatch.setattr(r, "generate_full_qa_plan", lambda *a: calls.append(a) or plan)
    store = {}
    monkeypatch.setattr(r.qa_plan_service, "save_qa_plan", lambda repo, api, v, p: store.update({v: {"plan": p}}))
    monkeypatch.setattr(r.qa_plan_service, "get_qa_plan", lambda repo, api, v: store.get(v))
    url = "/ui/spring-shop/OrderController.create/qa-plan"

    # no cached plan: answer at once with a progress page, nothing generated yet
    loading = two_versions.get(url).text
    assert 'role="status"' in loading and "Generating the test plan for v2" in loading
    assert 'data-api-url="/api/qa-plan?repo=spring-shop&api=OrderController.create&v1=1&v2=2"' in html.unescape(loading)
    assert not calls

    # the page's script calls the JSON endpoint, then reloads the cached plan
    assert two_versions.get("/api/qa-plan", params={"repo": "spring-shop", "api": "OrderController.create", "v1": 1, "v2": 2}).json() == plan
    assert len(calls) == 1
    page = two_versions.get(url).text
    assert "81%" in page and "Generating the test plan" not in page

    # regenerate goes through the progress page again, with force
    assert "Regenerating the test plan" in two_versions.get(url, params={"force": "true"}).text
    assert len(calls) == 1


def _layered_model():
    nodes = [
        {"id": "module:gateway", "type": "module", "name": "gateway", "path": "gateway", "spring_boot_app": True},
        {"id": "module:orders", "type": "module", "name": "orders", "path": "orders", "spring_boot_app": True, "port": 8081},
        {"id": "module:billing", "type": "module", "name": "billing", "path": "billing", "spring_boot_app": True},
        {"id": "module:common", "type": "module", "name": "common", "path": "common", "spring_boot_app": False},
        {"id": "component:o.OrderController", "type": "component", "name": "OrderController", "module": "orders", "layer": "web", "stereotype": "rest_controller"},
        {"id": "component:o.OrderService", "type": "component", "name": "OrderService", "module": "orders", "layer": "service", "stereotype": "service"},
        {"id": "component:o.OrderMapper", "type": "component", "name": "OrderMapper", "module": "orders", "layer": "data", "stereotype": "repository"},
        {"id": "component:o.OrderConfig", "type": "component", "name": "OrderConfig", "module": "orders", "layer": "config", "stereotype": "configuration"},
        {"id": "store:orders:mybatis", "type": "datastore", "technology": "mybatis", "module": "orders", "profiles": ["dev: mysql"]},
        {"id": "datastore:mysql:db/shop", "type": "datastore", "technology": "mysql", "host": "db", "database": "shop"},
        {"id": "broker:rabbitmq", "type": "broker", "technology": "rabbitmq"},
        {"id": "infra:discovery:eureka", "type": "infrastructure", "technology": "eureka"},
    ]
    edges = [
        {"from": "module:gateway", "to": "module:orders", "kind": "routes", "details": ["Path=/orders/**"]},
        {"from": "component:o.OrderController", "to": "component:o.OrderService", "kind": "calls"},
        {"from": "component:o.OrderService", "to": "component:o.OrderMapper", "kind": "calls"},
        {"from": "component:o.OrderService", "to": "module:billing", "kind": "http", "details": ["POST http://billing/charges"]},
        {"from": "component:o.OrderService", "to": "store:orders:mybatis", "kind": "write"},
        {"from": "store:orders:mybatis", "to": "datastore:mysql:db/shop", "kind": "connects"},
        {"from": "component:o.OrderService", "to": "module:common", "kind": "calls"},
    ]
    for m in ("gateway", "orders", "billing"):
        edges.append({"from": f"module:{m}", "to": "infra:discovery:eureka", "kind": "registers"})
        edges.append({"from": f"module:{m}", "to": "broker:rabbitmq", "kind": "connects"})
    return {"graph": {"nodes": nodes, "edges": edges},
            "modules": [{"path": "orders", "endpoint_count": 3}]}


def test_layered_system_view_is_clean():
    from app.services.architecture_view import layered_system_view
    view = layered_system_view(_layered_model())
    columns = {c["title"]: [n["label"] for n in c["nodes"]] for c in view["columns"]}

    assert columns["Entry points"] == ["gateway"]
    assert set(columns["Services"]) == {"orders", "billing", "common"}
    assert columns["Data & infrastructure"] == ["mysql @ db"]          # mybatis folded into its service
    platform = {p["label"]: p["used_by"] for p in view["platform"]}
    assert len(platform["eureka"]) == 3 and "rabbitmq (message bus)" in platform

    links = {(l["source"], l["target"]): l for l in view["links"]}
    assert links[("module:orders", "module:billing")]["category"] == "call"
    assert links[("module:orders", "module:common")]["category"] == "library"
    assert links[("module:orders", "datastore:mysql:db/shop")]["category"] == "data"
    assert not any("eureka" in l["target"] or "rabbitmq" in l["target"] for l in view["links"])
    orders = next(n for c in view["columns"] for n in c["nodes"] if n["id"] == "module:orders")
    assert "3 endpoints" in orders["meta"] and "mybatis" in orders["meta"]


def test_layered_component_view_hides_configuration():
    from app.services.architecture_view import layered_component_view
    view = layered_component_view(_layered_model(), "orders")
    columns = {c["title"]: [n["label"] for n in c["nodes"]] for c in view["columns"]}
    assert columns["Entry points"] == ["OrderController"]
    assert columns["Services"] == ["OrderService"]
    assert columns["Repositories & clients"] == ["OrderMapper"]
    assert set(columns["Systems"]) == {"billing", "mysql via mybatis"}
    assert view["hidden"] == 1


def test_architecture_page_uses_the_layered_diagram(two_versions):
    page = two_versions.get("/ui/spring-shop/architecture").text
    assert "/static/arch-diagram.js" in page and "d3.min.js" not in page
    layered = two_versions.get("/api/architecture/layered", params={"repo": "spring-shop"}).json()
    assert [c["title"] for c in layered["columns"]][-1] == "Data & infrastructure"
    assert two_versions.get("/api/architecture/layered", params={"repo": "nope"}).status_code == 404


def test_health_endpoint(client):
    assert client.get("/healthz").json() == {"status": "ok", "database": "ok", "version": client.get("/healthz").json()["version"]}


# ============================
# SECURITY: untrusted doc content, CSP
# ============================

XSS = """
<script>alert('doc')</script>
<img src=x onerror="alert('img')">
[click](javascript:alert('link'))
<a href="https://example.com" onclick="alert('a')">site</a>

| field | rule |
|---|---|
| name | &lt;script&gt;alert('cell')&lt;/script&gt; |
"""


def _poison(api, version, text):
    from sqlalchemy import update
    from app import db
    table = db.api_versions
    with db.engine().begin() as conn:
        conn.execute(update(table).where(table.c.repo == "spring-shop", table.c.api == api, table.c.version == version)
                     .values(content=table.c.content + text))


def _no_live_script(html_text):
    import re
    body = html_text.split("<main", 1)[1]
    assert "alert(" not in re.sub(r"&lt;script&gt;alert\('cell'\)&lt;/script&gt;", "", body), "unescaped payload in page"
    assert "onerror" not in body and "onclick" not in body and "javascript:" not in body


def test_doc_pages_strip_scripts_from_untrusted_markdown(two_versions):
    _poison("OrderController.create", 2, XSS)
    page = two_versions.get("/ui/spring-shop/OrderController.create").text
    _no_live_script(page)
    assert 'href="https://example.com"' in page          # harmless markup survives


def test_diff_does_not_turn_escaped_text_into_markup(two_versions, monkeypatch):
    from app.api import routes as r
    monkeypatch.setattr(r, "summarize_changes", lambda *a: None)
    _poison("OrderController.create", 1, "\n| field | rule |\n|---|---|\n| name | plain |\n")
    _poison("OrderController.create", 2, XSS)
    page = two_versions.get("/ui/spring-shop/OrderController.create/diff", params={"v1": 1, "v2": 2}).text
    _no_live_script(page)


def test_pages_carry_a_nonce_csp_that_matches_every_script(two_versions):
    import re
    response = two_versions.get("/ui/spring-shop/OrderController.create/qa-plan")
    csp = response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert "script-src 'self' 'nonce-" in csp and "frame-ancestors 'none'" in csp
    scripts = re.findall(r"<script[^>]*>", response.text)
    assert scripts and all(f'nonce="{nonce}"' in s for s in scripts)
    assert not re.search(r"\son(click|change|submit|load|error)=", response.text)
    assert response.headers["x-content-type-options"] == "nosniff"
    # a fresh nonce per response
    again = two_versions.get("/ui/spring-shop/OrderController.create/qa-plan").headers["content-security-policy"]
    assert again != csp
    # JSON APIs get the hardening headers but no page CSP
    assert "content-security-policy" not in two_versions.get("/healthz").headers


def test_uploads_are_queued_and_visible_until_documented(client, isolated_storage):
    payload = json.loads(SPRING_SCAN.read_text())
    queued = client.post("/analyze", json=payload, headers=bearer(client))
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    assert client.post("/analyze", json=payload, headers=bearer(client)).json() == {**queued.json(), "status": "already_queued"}

    job = client.get(f"/api/jobs/{job_id}").json()
    assert (job["status"], job["repo"], job["commit"]) == ("queued", "spring-shop", "abc1234")
    assert [j["id"] for j in client.get("/api/jobs", params={"active": True}).json()["jobs"]] == [job_id]

    # nothing documented yet: the dashboard and the repo page show the upload in progress
    assert "Processing uploads" in client.get("/ui").text
    repo_page = client.get("/ui/spring-shop")
    assert repo_page.status_code == 200 and "Processing uploads" in repo_page.text and "/static/jobs.js" in repo_page.text

    jobs.run_pending()
    done = client.get(f"/api/jobs/{job_id}").json()
    assert done["status"] == "done" and done["result"] == {"created": 4, "unchanged": 0, "endpoints": 4}
    assert (done["progress_done"], done["progress_total"]) == (4, 4)
    assert "Processing uploads" not in client.get("/ui").text
    assert client.get("/api/jobs/999999").status_code == 404
