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

    redirect = client.get("/ui/shop", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/ui/shop/get_item"

    assert client.get("/ui/shop/missing").status_code == 404


def test_template_names_cannot_escape_templates_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(test_templates, "TEMPLATES_DIR", str(tmp_path / "templates"))

    assert test_templates.create_template("shop", "../../evil", "x", "y", []) is False
    assert test_templates.create_template("..", "ok", "x", "y", []) is False
    assert test_templates.create_template("shop", "Happy Path", "x", "y", ["a"]) is True
    assert test_templates.get_template("shop", "Happy Path")["test_count"] == 1
