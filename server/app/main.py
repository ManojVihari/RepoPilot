from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app.config import STATIC_DIR
from mergeclear import __version__

app = FastAPI(title="Mergeclear")

app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness for load balancers and container orchestrators."""
    return {"status": "ok", "version": __version__}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
