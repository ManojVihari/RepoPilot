"""Sign-in, first-run setup, roles, API keys and CSRF."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app

SCAN = json.loads((Path(__file__).parent / "fixtures" / "spring_shop_scan.json").read_text())


def browser():
    return TestClient(app, follow_redirects=False)


def sign_in(email, password):
    b = browser()
    response = b.post("/login", data={"email": email, "password": password})
    assert response.status_code == 303, response.text
    return b, response


def csrf(page_html):
    return re.search(r'name="csrf_token" value="([^"]+)"', page_html).group(1)


@pytest.fixture
def admin():
    auth.create_user("admin@example.com", "Ada Admin", "admin", "admin-password-1")
    b, _ = sign_in("admin@example.com", "admin-password-1")
    return b


@pytest.fixture
def viewer(admin):
    auth.create_user("viewer@example.com", "Vic Viewer", "viewer", "viewer-password-1")
    b, _ = sign_in("viewer@example.com", "viewer-password-1")
    return b


# ---------------------------------------------------------------- first run

def test_a_fresh_server_asks_for_the_admin_account_once():
    b = browser()
    assert b.get("/ui").headers["location"] == "/setup"
    assert b.get("/login").headers["location"] == "/setup"
    assert b.get("/api/jobs").status_code == 503
    assert b.get("/").status_code == 200                    # the product page stays public

    short = b.post("/setup", data={"name": "Ada", "email": "ada@example.com", "password": "short", "confirm": "short"})
    assert short.status_code == 400 and "at least 10 characters" in short.text

    done = b.post("/setup", data={"name": "Ada", "email": "Ada@Example.com", "password": "admin-password-1",
                                  "confirm": "admin-password-1"})
    assert done.status_code == 303 and done.headers["location"] == "/ui"
    assert b.get("/ui").status_code == 200                   # signed in right away
    user = auth.get_user_by_email("ada@example.com")
    assert user["role"] == "admin" and "admin-password-1" not in user["password_hash"]

    # closed for good
    assert browser().get("/setup").headers["location"] == "/login"
    again = browser().post("/setup", data={"name": "Eve", "email": "eve@example.com", "password": "x" * 12, "confirm": "x" * 12})
    assert again.headers["location"] == "/login" and auth.get_user_by_email("eve@example.com") is None


# ---------------------------------------------------------------- signing in

def test_pages_need_a_sign_in_and_return_after_it(admin):
    b = browser()
    page = b.get("/ui/shop/x/history?v=1")
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/ui/shop/x/history%3Fv%3D1"
    assert b.get("/api/jobs").status_code == 401
    assert b.get("/static/app.css").status_code == 200 and b.get("/healthz").status_code == 200

    wrong = b.post("/login", data={"email": "admin@example.com", "password": "nope", "next": "/ui/all"})
    assert wrong.status_code == 401 and "Email or password is not correct" in wrong.text
    ok = b.post("/login", data={"email": "ADMIN@example.com", "password": "admin-password-1", "next": "/ui/all"})
    assert ok.headers["location"] == "/ui/all"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie

    # no open redirects after sign-in
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        assert browser().post("/login", data={"email": "admin@example.com", "password": "admin-password-1",
                                              "next": evil}).headers["location"] == "/ui"


def test_repeated_failures_lock_the_account_for_a_while(admin):
    b = browser()
    for _ in range(5):
        assert b.post("/login", data={"email": "admin@example.com", "password": "wrong"}).status_code == 401
    locked = b.post("/login", data={"email": "admin@example.com", "password": "admin-password-1"})
    assert locked.status_code == 401 and "Too many failed attempts" in locked.text
    auth.throttle.reset("email:admin@example.com", "ip:testclient")


def test_sign_out_needs_the_csrf_token_and_ends_the_session(admin):
    page = admin.get("/ui").text
    assert admin.post("/logout", data={"csrf_token": "forged"}).status_code == 403
    assert admin.get("/ui").status_code == 200
    assert admin.post("/logout", data={"csrf_token": csrf(page)}).headers["location"] == "/login"
    assert admin.get("/ui").status_code == 303


# ---------------------------------------------------------------- users added by admins

def test_admin_adds_a_user_who_must_choose_a_password(admin):
    page = admin.get("/settings/users").text
    added = admin.post("/settings/users", data={"name": "Val", "email": "val@example.com", "role": "viewer",
                                                "csrf_token": csrf(page)})
    assert added.status_code == 200 and "Account created for val@example.com" in added.text
    temporary = re.search(r'id="issued-password">([^<]+)<', added.text).group(1)

    val, response = sign_in("val@example.com", temporary)
    assert response.headers["location"] == "/settings?must_change=1"
    assert val.get("/ui").headers["location"] == "/settings?must_change=1"     # nothing else until changed
    settings = val.get("/settings?must_change=1").text
    assert "Choose your own password" in settings

    changed = val.post("/settings/password", data={"current": temporary, "password": "my-own-password",
                                                   "confirm": "my-own-password", "csrf_token": csrf(settings)})
    assert changed.headers["location"] == "/settings?saved=password"
    assert val.get("/ui").status_code == 200
    assert auth.get_user_by_email("val@example.com")["must_change_password"] is False


def test_admin_changes_roles_deactivates_and_keeps_one_admin(admin, viewer):
    page = admin.get("/settings/users").text
    token = csrf(page)
    vic = auth.get_user_by_email("viewer@example.com")
    me = auth.get_user_by_email("admin@example.com")

    assert admin.post(f"/settings/users/{vic['id']}", data={"action": "role", "role": "admin", "csrf_token": token}).status_code == 303
    assert auth.get_user(vic["id"])["role"] == "admin"
    admin.post(f"/settings/users/{vic['id']}", data={"action": "role", "role": "viewer", "csrf_token": token})

    # the only admin cannot demote or deactivate themself
    demote = admin.post(f"/settings/users/{me['id']}", data={"action": "role", "role": "viewer", "csrf_token": token})
    assert demote.status_code == 400 and "at least one active admin" in demote.text
    assert "cannot deactivate your own account" in admin.post(
        f"/settings/users/{me['id']}", data={"action": "deactivate", "csrf_token": token}).text

    # deactivating ends the user's sessions at once
    assert viewer.get("/ui").status_code == 200
    admin.post(f"/settings/users/{vic['id']}", data={"action": "deactivate", "csrf_token": token})
    assert viewer.get("/ui").status_code == 303
    assert browser().post("/login", data={"email": "viewer@example.com", "password": "viewer-password-1"}).status_code == 401

    # forged requests are refused
    assert admin.post(f"/settings/users/{vic['id']}", data={"action": "activate"}).status_code == 403


def test_signup_is_off_unless_enabled(admin, monkeypatch):
    assert browser().get("/signup").status_code == 404
    from app.api import auth_routes
    monkeypatch.setattr(auth_routes, "ALLOW_SIGNUP", True)
    b = browser()
    created = b.post("/signup", data={"name": "Sam", "email": "sam@example.com", "password": "sam-password-1",
                                      "confirm": "sam-password-1"})
    assert created.headers["location"] == "/ui"
    assert auth.get_user_by_email("sam@example.com")["role"] == "viewer"      # never admin


# ---------------------------------------------------------------- roles

def test_viewers_read_but_do_not_change(viewer):
    assert viewer.get("/ui").status_code == 200
    assert viewer.get("/settings/users").status_code == 403
    page = viewer.get("/ui/search").text
    assert "/settings/users" not in viewer.get("/ui").text                 # no admin menu entry

    template = viewer.post("/api/templates/create?repo=shop&name=x&category=c&description=d",
                           json=["case"], headers={"X-CSRF-Token": re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)})
    assert template.status_code == 403 and "admin" in template.json()["error"]


def test_browser_requests_that_change_things_need_the_csrf_header(admin):
    page = admin.get("/ui/search").text
    token = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
    url = "/api/templates/create?repo=shop&name=Smoke&category=happy_path&description=d"
    assert admin.post(url, json=["a"]).status_code == 403
    assert admin.post(url, json=["a"], headers={"X-CSRF-Token": token}).json()["success"] is True


# ---------------------------------------------------------------- API keys

def test_api_keys_are_shown_once_work_for_uploads_and_can_be_revoked(admin):
    page = admin.get("/settings/api-keys").text
    created = admin.post("/settings/api-keys", data={"name": "Jenkins", "csrf_token": csrf(page)})
    key = re.search(r'id="new-key-value">([^<]+)<', created.text).group(1)
    assert key.startswith("mc_") and "MERGECLEAR_API_KEY=" in created.text
    assert key not in admin.get("/settings/api-keys").text                 # never shown again
    assert key.split("_")[2] not in json.dumps(auth.list_api_keys(), default=str)     # secret not stored

    scanner = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    assert scanner.post("/analyze", json=SCAN, headers=headers).status_code == 202
    assert scanner.get("/api/jobs", headers=headers).status_code == 200
    assert auth.list_api_keys()[0]["last_used_at"] is not None

    assert scanner.post("/analyze", json=SCAN, headers={"Authorization": "Bearer mc_x_wrong"}).status_code == 401
    assert scanner.post("/analyze", json=SCAN).status_code == 401

    key_id = auth.list_api_keys()[0]["id"]
    admin.post(f"/settings/api-keys/{key_id}/revoke", data={"csrf_token": csrf(page)})
    assert scanner.post("/analyze", json=SCAN, headers=headers).status_code == 401


def test_a_viewer_key_reads_but_cannot_upload(viewer):
    vic = auth.get_user_by_email("viewer@example.com")
    key = auth.create_api_key(vic["id"], "notebook")["key"]
    scanner = TestClient(app)
    headers = {"Authorization": f"Bearer {key}"}
    assert scanner.get("/api/jobs", headers=headers).status_code == 200
    upload = scanner.post("/analyze", json=SCAN, headers=headers)
    assert upload.status_code == 403 and "admin" in upload.json()["error"]
    # nor revoke other people's keys
    admin_id = auth.get_user_by_email("admin@example.com")["id"]
    other = auth.create_api_key(admin_id, "ci")
    assert auth.revoke_api_key(other["id"], auth.principal_from_api_key(key)) is False


def test_regenerating_a_qa_plan_is_for_admins(admin, viewer, monkeypatch):
    from app.api import routes
    from app import jobs
    monkeypatch.setattr(routes, "generate_full_qa_plan", lambda *a: {"coverage": {}, "test_cases": {}, "is_template": True})
    admin_key = auth.create_api_key(auth.get_user_by_email("admin@example.com")["id"], "ci")["key"]
    assert TestClient(app).post("/analyze", json=SCAN, headers={"Authorization": f"Bearer {admin_key}"}).status_code == 202
    jobs.run_pending()

    url = "/ui/spring-shop/OrderController.create/qa-plan"
    # a viewer's first visit generates and caches the plan (the progress page calls /api/qa-plan)
    assert viewer.get("/api/qa-plan?repo=spring-shop&api=OrderController.create").status_code == 200
    page = viewer.get(url)
    assert page.status_code == 200 and "Test plan for v1" in page.text
    assert "force=true" not in page.text                      # no Regenerate button for viewers
    assert "force=true" in admin.get(url).text
    assert viewer.get(url + "?force=true").status_code == 403
    assert viewer.get("/api/qa-plan?repo=spring-shop&api=OrderController.create&force=true").status_code == 403
    assert admin.get(url + "?force=true").status_code == 200
