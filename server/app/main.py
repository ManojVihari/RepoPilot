import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app import db, jobs
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

app.include_router(router)

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


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Health for load balancers and container orchestrators (503 when the database is unreachable)."""
    if not db.healthy():
        return JSONResponse({"status": "error", "database": "unreachable", "version": __version__}, status_code=503)
    return {"status": "ok", "database": "ok", "version": __version__}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
