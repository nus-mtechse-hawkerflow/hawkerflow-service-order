from contextlib import asynccontextmanager
from pathlib import Path
import os

from fastapi import FastAPI
from sqlmodel import SQLModel

from configurations.app_config import AppConfig
from session.db_session import DBSession
from services.order_service import OrderService
from repository.order_repo import OrderRepo


@asynccontextmanager
async def startup(app: FastAPI):
    project_root = Path(__file__).resolve().parents[2]
    os.environ.setdefault("PROJECT_PATH", str(project_root))

    config = AppConfig()
    session = DBSession(config.datasource)
    SQLModel.metadata.create_all(session.engine)

    order_repo = OrderRepo(session.engine)
    order_service = OrderService(order_repo)

    app.state.config = config
    app.state.session = session
    app.state.order_service = order_service

    yield
