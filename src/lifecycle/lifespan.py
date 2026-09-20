import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import os

from fastapi import FastAPI
from sqlmodel import SQLModel

from configurations.app_config import AppConfig
from session.db_session import DBSession
from services.order_service import OrderService
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher
from workers.sqs_worker import SqsWorker

logger = logging.getLogger("hawkerflow-order.lifecycle")


@asynccontextmanager
async def startup(app: FastAPI):
    project_root = Path(__file__).resolve().parents[2]
    os.environ.setdefault("PROJECT_PATH", str(project_root))

    config = AppConfig()
    session = DBSession(config.datasource)
    SQLModel.metadata.create_all(session.engine)

    order_repo = OrderRepo(session.engine)
    event_publisher = EventPublisher(config.events) if config.events else None
    order_service = OrderService(order_repo, event_publisher=event_publisher)

    app.state.config = config
    app.state.session = session
    app.state.event_publisher = event_publisher
    app.state.order_service = order_service

    # Start background SQS worker if enabled in configuration
    worker = None
    worker_task = None
    if config.sqs and config.sqs.enabled and config.sqs.queue_url:
        logger.info(
            "🚀 Initializing background SQS worker on queue: %s (region: %s)",
            config.sqs.queue_url,
            config.sqs.region_name,
        )
        try:
            worker = SqsWorker(config.sqs, order_service)
            app.state.sqs_worker = worker
            worker_task = asyncio.create_task(worker.start())
        except Exception as e:
            logger.exception("❌ Failed to start SQS Worker during lifespan startup: %s", e)
    else:
        logger.warning(
            "⚠️ SQS Background Worker is DISABLED (sqs.enabled=false or queue_url is empty). "
            "Set sqs.enabled: true in resources/config.yml to enable."
        )
        app.state.sqs_worker = None

    yield

    # Clean shutdown of background worker
    if worker and worker_task:
        logger.info("Shutting down background SQS Worker...")
        worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("SQS Worker shutdown complete.")
