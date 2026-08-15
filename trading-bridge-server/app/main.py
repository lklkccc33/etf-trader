from contextlib import asynccontextmanager

from fastapi import FastAPI

from .ip_allowlist import TrustedNetworkMiddleware
from .logging_config import configure_logging
from .routers import balance, orders, sessions
from .scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Trading Bridge Server", lifespan=lifespan)
app.add_middleware(TrustedNetworkMiddleware)

app.include_router(sessions.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(balance.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
