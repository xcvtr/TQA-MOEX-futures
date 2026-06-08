"""Dashboard v2 — FastAPI app factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path

app = FastAPI(title='MOEX Dashboard v2', version='2.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

from .adapters import moex_strategies  # noqa: auto-register strategies

from .routers.live import router as live_router
from .routers.backtest import router as backtest_router
from .routers.portfolio import router as portfolio_router
from .routers.data import router as data_router

app.include_router(live_router)
app.include_router(backtest_router)
app.include_router(portfolio_router)
app.include_router(data_router)


frontend_dir = Path(__file__).parent / 'frontend'


@app.get('/')
async def index():
    return FileResponse(str(frontend_dir / 'index.html'))
