import asyncio
import logging
import os
from pathlib import Path
import signal
import sys

from sqlmodel import SQLModel

from configurations.app_config import AppConfig
from repository.order_repo import OrderRepo
from services.order_service import OrderService
from session.db_session import DBSession
from workers.sqs_worker import SqsWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hawkerflow-order-worker")


async def main():
    project_root = Path(__file__).resolve().parents[1]
    os.environ.setdefault("PROJECT_PATH", str(project_root))

    logger.info("Initializing standalone Order SQS Background Worker...")
    config = AppConfig()

    if not config.sqs or not config.sqs.queue_url:
        logger.error("SQS configuration (queue_url) missing in config.yml. Exiting.")
        sys.exit(1)

    session = DBSession(config.datasource)
    SQLModel.metadata.create_all(session.engine)

    order_repo = OrderRepo(session.engine)
    order_service = OrderService(order_repo)

    worker = SqsWorker(config.sqs, order_service)

    # Register OS signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def handle_signal():
        logger.info("Received termination signal. Shutting down worker...")
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Signal handlers might not be supported on some platforms (e.g. Windows event loop)
            pass

    logger.info("Worker initialized. Starting polling loop...")
    await worker.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker process exited.")
