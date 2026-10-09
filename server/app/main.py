import contextlib
import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app import auth, db, jobs
from app.limits import BodySizeLimit
from app.api.auth_routes import router as auth_router
from app.config import STATIC_DIR, WORKERS
from app.services.html_safety import SECURITY_HEADERS, content_security_policy, new_nonce
from mergeclear import __version__

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mergeclear")


@contextlib.asynccontextmanager
async def lifespan(_app):
    db.engine()                      # connect and create tables now, not on the first request
    logger.info("database: %s", db.describe())
    jobs.start_workers(WORKERS)
    logger.info("background workers: %d", WORKERS)
    yield
    jobs.stop_workers()


app = FastAPI(title="Mergeclear", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(router)

# reachable without signing in
PUBLIC_PATHS = {"/", "/login", "/logout", "/setup", "/signup", "/healthz", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)
# answered with 401 JSON instead of a redirect to the sign-in page
API_PREFIXES = ("/api/", "/analyze", "/openapi.json")
# reachable while a temporary password must still be changed
PASSWORD_CHANGE_PATHS = {"/settings", "/settings/password", "/logout"}
_has_users = False


def _users_exist() -> bool:
    global _has_users
    if not _has_users:
        _has_users = auth.count_users() > 0      # once true it stays true: the last admin cannot be removed
    return _has_users


@app.middleware("http")
async def authentication(request: Request, call_next):
    """Who is asking (session cookie or API key), and keep everything but public pages behind sign-in."""
    path = request.url.path
    public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
    skip = path.startswith(PUBLIC_PREFIXES) or path == "/healthz"      # health must not depend on sessions
    request.state.user = None if skip else await run_in_threadpool(auth.resolve, request)
    is_api = path.startswith(API_PREFIXES)

    if not public:
        if not await run_in_threadpool(_users_exist):
            if is_api:
                return JSONResponse({"error": "this server is not set up yet: open it in a browser to create the admin account"}, status_code=503)
            return RedirectResponse("/setup", status_code=303)
        user = request.state.user
        if user is None:
            if is_api or request.method != "GET":
                return JSONResponse({"error": "sign in or send an API key (Authorization: Bearer ...)"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
            target = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
        if user.must_change_password and path not in PASSWORD_CHANGE_PATHS and not is_api:
            return RedirectResponse("/settings?must_change=1", status_code=303)

    return await call_next(request)

# FastAPI's own API explorer loads its assets from a CDN; it keeps its defaults
NO_CSP_PATHS = ("/docs", "/redoc")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Per-request script nonce for the templates, CSP and the usual hardening headers."""
    request.state.csp_nonce = new_nonce()
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if response.headers.get("content-type", "").startswith("text/html") and not request.url.path.startswith(NO_CSP_PATHS):
        response.headers["Content-Security-Policy"] = content_security_policy(request.state.csp_nonce)
    return response


# outermost: refuse oversized bodies before anything reads them
app.add_middleware(BodySizeLimit)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Health for load balancers and container orchestrators (503 when the database is unreachable)."""
    if not db.healthy():
        return JSONResponse({"status": "error", "database": "unreachable", "version": __version__}, status_code=503)
    return {"status": "ok", "database": "ok", "version": __version__}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
