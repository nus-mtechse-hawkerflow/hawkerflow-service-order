import asyncio
import json
import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from configurations.app_config import SqsConfig
from endpoints.order_routes import get_queued_order, queue_order
from models.order_details import Dish, OrderDetails
from models.order_details import Order as OrderDto
from repository.order_repo import OrderRepo
from services.order_queue_producer import OrderQueueProducer
from services.order_service import OrderService
from workers.sqs_worker import SqsWorker


def _order_details() -> OrderDetails:
    return OrderDetails(
        orders=[
            OrderDto(
                stall_id=1,
                dishes=[Dish(dish_id=1, dish_name="Steamed Chicken Rice", quantity=2, price=4.50)],
            )
        ],
        total_price=9.00,
    )


class TestOrderQueueIntake(unittest.TestCase):
    """
    Diner orders placed through order_queue: the API enqueues and answers 202
    with an order_ref, the SQS worker creates the order, and the diner looks
    the order_id up by that ref.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.service = OrderService(OrderRepo(self.engine))

        self.sqs_config = SqsConfig(
            enabled=True,
            queue_url="https://sqs.ap-southeast-1.amazonaws.com/123456789012/order_queue",
            region_name="ap-southeast-1",
        )
        self.sqs = MagicMock()
        self.producer = OrderQueueProducer(self.sqs_config, sqs_client=self.sqs)
        self.worker = SqsWorker(self.sqs_config, self.service, sqs_client=MagicMock())

    def _sent_body(self) -> dict:
        self.sqs.send_message.assert_called_once()
        kwargs = self.sqs.send_message.call_args.kwargs
        self.assertEqual(kwargs["QueueUrl"], self.sqs_config.queue_url)
        return json.loads(kwargs["MessageBody"])

    def _deliver(self, body: dict, message_id: str = "msg-1") -> None:
        asyncio.run(self.worker._handle_message({
            "MessageId": message_id,
            "ReceiptHandle": f"receipt-{message_id}",
            "Body": json.dumps(body),
        }))

    def test_producer_sends_order_placed_message_carrying_the_order_ref(self):
        order_ref = asyncio.run(self.producer.enqueue_order(_order_details()))

        body = self._sent_body()
        self.assertEqual(body["event_type"], "ORDER_PLACED")
        self.assertEqual(body["order_ref"], order_ref)
        self.assertEqual(body["data"]["total_price"], 9.00)
        self.assertEqual(body["data"]["orders"][0]["stall_id"], 1)

    def test_producer_gives_each_order_a_distinct_ref(self):
        first = asyncio.run(self.producer.enqueue_order(_order_details()))
        second = asyncio.run(self.producer.enqueue_order(_order_details()))

        self.assertNotEqual(first, second)

    def test_queued_order_is_pending_until_the_worker_creates_it(self):
        order_ref = asyncio.run(self.producer.enqueue_order(_order_details()))

        self.assertIsNone(self.service.get_queued_order(order_ref))

        self._deliver(self._sent_body())

        queued = self.service.get_queued_order(order_ref)
        self.assertIsNotNone(queued)
        created = self.service.get_order(queued["order_id"])
        self.assertEqual(created["total_order_price"], 9.00)
        self.assertEqual(len(self.service.get_orders_for_stall(1)), 1)

    def test_redelivered_message_does_not_create_a_second_order(self):
        """SQS delivers at least once: the same message can arrive twice."""
        asyncio.run(self.producer.enqueue_order(_order_details()))
        body = self._sent_body()

        self._deliver(body, message_id="msg-1")
        self._deliver(body, message_id="msg-1-redelivered")

        self.assertEqual(len(self.service.get_orders_for_stall(1)), 1)
        # The duplicate is handled, not failed: it must leave the queue rather
        # than be retried until it lands in the dead-letter queue.
        deleted = [c.kwargs["ReceiptHandle"] for c in self.worker._sqs.delete_message.call_args_list]
        self.assertEqual(deleted, ["receipt-msg-1", "receipt-msg-1-redelivered"])

    def test_queue_endpoint_answers_202_with_the_order_ref(self):
        response = asyncio.run(queue_order(orders=_order_details(), producer=self.producer))

        self.assertEqual(response.status_code, 202)
        data = json.loads(response.body.decode())
        self.assertEqual(data["status"], "QUEUED")
        self.assertEqual(data["order_ref"], self._sent_body()["order_ref"])

    def test_queue_endpoint_answers_503_when_the_queue_is_not_configured(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(queue_order(orders=_order_details(), producer=None))

        self.assertEqual(ctx.exception.status_code, 503)

    def test_lookup_endpoint_answers_202_pending_then_200_with_the_order_id(self):
        order_ref = asyncio.run(self.producer.enqueue_order(_order_details()))

        pending = asyncio.run(get_queued_order(order_ref=order_ref, order_service=self.service))
        self.assertEqual(pending.status_code, 202)
        self.assertEqual(json.loads(pending.body.decode())["status"], "PENDING")

        self._deliver(self._sent_body())

        created = asyncio.run(get_queued_order(order_ref=order_ref, order_service=self.service))
        self.assertEqual(created.status_code, 200)
        data = json.loads(created.body.decode())
        self.assertEqual(data["status"], "CREATED")
        self.assertEqual(data["order_ref"], order_ref)
        self.assertEqual(data["order_id"], self.service.get_queued_order(order_ref)["order_id"])


if __name__ == "__main__":
    unittest.main()
