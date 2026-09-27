import asyncio
import json
import logging
import uuid
from typing import Any

from configurations.app_config import SqsConfig
from models.order_details import OrderDetails
from workers.sqs_worker import create_sqs_client

logger = logging.getLogger("hawkerflow-order.order_queue_producer")


class OrderQueueProducer:
    """
    Places diner orders on the inbound order queue for SqsWorker to create.
    Executes the boto3 send off the main event loop via asyncio.to_thread.
    """

    def __init__(self, config: SqsConfig, sqs_client: Any = None):
        self.config = config
        self._sqs = sqs_client if sqs_client is not None else create_sqs_client(config)

    async def enqueue_order(self, order: OrderDetails) -> str:
        """
        Sends an ORDER_PLACED message and returns its order_ref, which the diner
        uses to look up the order_id once the worker has created the order.
        """
        order_ref = str(uuid.uuid4())
        body = {
            "event_type": "ORDER_PLACED",
            "order_ref": order_ref,
            "data": order.model_dump(),
        }

        await asyncio.to_thread(
            self._sqs.send_message,
            QueueUrl=self.config.queue_url,
            MessageBody=json.dumps(body),
        )
        logger.info("Queued order %s on %s", order_ref, self.config.queue_url)
        return order_ref
