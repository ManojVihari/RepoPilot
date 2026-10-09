"""Request size cap and the per-account upload limit."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import auth, limits
from app.limits import BodySizeLimit, RateLimit
from app.main import app

SCAN = json.loads((Path(__file__).parent / "fixtures" / "spring_shop_scan.json").read_text())


def admin_key():
    user = auth.create_user("admin@example.com", "Ada", "admin", "admin-password-1")
    return {"Authorization": f"Bearer {auth.create_api_key(user['id'], 'ci', 'shop')['key']}"}


def test_oversized_bodies_are_refused_declared_or_streamed():
    async def echo(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(len(body)).encode()})

    client = TestClient(BodySizeLimit(echo, max_bytes=100))
    assert client.post("/", content=b"x" * 100).text == "100"
    declared = client.post("/", content=b"x" * 101)
    assert declared.status_code == 413 and "MERGECLEAR_MAX_UPLOAD_MB" in declared.json()["error"]

    def chunks():                                     # no Content-Length: counted while it streams
        for _ in range(5):
            yield b"x" * 30

    assert client.post("/", content=chunks()).status_code == 413


def test_the_server_caps_upload_size(monkeypatch):
    headers = admin_key()
    middleware = next(m for m in app.user_middleware if m.cls is BodySizeLimit)
    monkeypatch.setitem(middleware.kwargs, "max_bytes", 1000)
    app.middleware_stack = None                       # rebuilt with the smaller limit
    try:
        response = TestClient(app).post("/analyze", json=SCAN, headers=headers)
        assert response.status_code == 413
    finally:
        monkeypatch.undo()
        app.middleware_stack = None
    assert TestClient(app).post("/analyze", json=SCAN, headers=headers).status_code == 202


def test_uploads_per_account_are_rate_limited(monkeypatch):
    headers = admin_key()
    monkeypatch.setattr(limits, "uploads", RateLimit(2))
    client = TestClient(app)
    assert client.post("/analyze", json={**SCAN, "commit": "a"}, headers=headers).status_code == 202
    assert client.post("/analyze", json={**SCAN, "commit": "b"}, headers=headers).status_code == 202
    third = client.post("/analyze", json={**SCAN, "commit": "c"}, headers=headers)
    assert third.status_code == 429 and 1 <= int(third.headers["Retry-After"]) <= 61
    assert "too many uploads" in third.json()["error"]
    assert client.get("/api/jobs", headers=headers).status_code == 200       # reading is not limited


def test_rate_limit_window_slides():
    limit = RateLimit(2)
    assert limit.check("k", now=0) == 0 and limit.check("k", now=10) == 0
    assert limit.check("k", now=20) == 41                 # oldest hit (t=0) leaves the window at t=60
    assert limit.check("other", now=20) == 0              # per key
    assert limit.check("k", now=60.5) == 0
    assert RateLimit(0).check("k") == 0                   # 0 turns it off
