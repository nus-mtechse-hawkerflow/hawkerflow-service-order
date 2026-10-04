import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlmodel import SQLModel

from configurations.app_config import AppConfig, SqsConfig
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher
from services.order_queue_producer import OrderQueueProducer
from services.order_service import OrderService
from session.db_session import DBSession
from workers.sqs_worker import SqsWorker

logger = logging.getLogger("hawkerflow-order.lifecycle")


def create_queue_components(
    sqs: SqsConfig | None,
    order_service: OrderService,
) -> tuple[OrderQueueProducer | None, SqsWorker | None]:
    """
    Builds the order queue producer and its background worker, or (None, None)
    when the queue is disabled or has no URL configured.
    """
    if not (sqs and sqs.enabled and sqs.queue_url):
        return None, None

    return OrderQueueProducer(sqs), SqsWorker(sqs, order_service)


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
    app.state.order_queue_producer = None
    app.state.sqs_worker = None
    try:
        app.state.order_queue_producer, worker = create_queue_components(config.sqs, order_service)
    except Exception as e:
        logger.exception("❌ Failed to set up the order queue during lifespan startup: %s", e)

    if worker:
        logger.info(
            "🚀 Initializing background SQS worker on queue: %s (region: %s)",
            config.sqs.queue_url,
            config.sqs.region_name,
        )
        app.state.sqs_worker = worker
        worker_task = asyncio.create_task(worker.start())
    else:
        logger.warning(
            "⚠️ SQS Background Worker is DISABLED (sqs.enabled=false or queue_url is empty). "
            "Set sqs.enabled: true in resources/config.yml to enable."
        )

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
