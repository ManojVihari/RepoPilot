from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.api.routes import router
from app.config import STATIC_DIR

app = FastAPI(title="DocAI Server")

app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
