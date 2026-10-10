import asyncio
import json
import unittest
from unittest.mock import MagicMock

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from configurations.app_config import SqsConfig
from endpoints.order_routes import get_queued_order
from models.order_details import Dish, OrderDetails
from models.order_details import Order as OrderDto
from repository.order_repo import OrderRepo
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


def _queued_message(order_ref: str = "ref-1") -> dict:
    """The message diner-ui posts and API Gateway puts on order_queue unchanged."""
    return {
        "event_type": "ORDER_PLACED",
        "order_ref": order_ref,
        "data": _order_details().model_dump(),
    }


class TestOrderQueueIntake(unittest.TestCase):
    """
    Diner orders placed through order_queue: API Gateway puts the diner's
    message on the queue, the SQS worker creates the order, and the diner
    looks the order_id up by the order_ref it chose.
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
        self.worker = SqsWorker(self.sqs_config, self.service, sqs_client=MagicMock())

    def _deliver(self, body: dict, message_id: str = "msg-1") -> None:
        asyncio.run(self.worker._handle_message({
            "MessageId": message_id,
            "ReceiptHandle": f"receipt-{message_id}",
            "Body": json.dumps(body),
        }))

    def test_queued_order_is_pending_until_the_worker_creates_it(self):
        order_ref = "ref-1"

        self.assertIsNone(self.service.get_queued_order(order_ref))

        self._deliver(_queued_message(order_ref))

        queued = self.service.get_queued_order(order_ref)
        self.assertIsNotNone(queued)
        created = self.service.get_order(queued["order_id"])
        self.assertEqual(created["total_order_price"], 9.00)
        self.assertEqual(len(self.service.get_orders_for_stall(1)), 1)

    def test_redelivered_message_does_not_create_a_second_order(self):
        """SQS delivers at least once: the same message can arrive twice."""
        body = _queued_message()

        self._deliver(body, message_id="msg-1")
        self._deliver(body, message_id="msg-1-redelivered")

        self.assertEqual(len(self.service.get_orders_for_stall(1)), 1)
        # The duplicate is handled, not failed: it must leave the queue rather
        # than be retried until it lands in the dead-letter queue.
        deleted = [c.kwargs["ReceiptHandle"] for c in self.worker._sqs.delete_message.call_args_list]
        self.assertEqual(deleted, ["receipt-msg-1", "receipt-msg-1-redelivered"])

    def test_two_orders_with_different_refs_are_both_created(self):
        self._deliver(_queued_message("ref-1"), message_id="msg-1")
        self._deliver(_queued_message("ref-2"), message_id="msg-2")

        self.assertEqual(len(self.service.get_orders_for_stall(1)), 2)
        self.assertNotEqual(
            self.service.get_queued_order("ref-1")["order_id"],
            self.service.get_queued_order("ref-2")["order_id"],
        )

    def test_malformed_order_is_left_on_the_queue_for_the_dead_letter_rule(self):
        """Nothing checks an order's shape before it is queued, so the worker must not delete a bad one."""
        self._deliver({"event_type": "ORDER_PLACED", "order_ref": "bad", "data": {"orders": "not a list"}})

        self.worker._sqs.delete_message.assert_not_called()
        self.assertIsNone(self.service.get_queued_order("bad"))
        self.assertEqual(self.service.get_orders_for_stall(1), [])

    def test_the_order_service_no_longer_queues_orders_itself(self):
        """API Gateway owns POST /orders/queue; only the look-up is left here."""
        from endpoints.order_routes import order_router

        queue_routes = {
            (tuple(sorted(route.methods)), route.path)
            for route in order_router.routes
            if "/orders/queue" in route.path
        }
        self.assertEqual(queue_routes, {(("GET",), "/v1/order/orders/queue/{order_ref}")})

    def test_lookup_endpoint_answers_202_pending_then_200_with_the_order_id(self):
        order_ref = "ref-1"

        pending = asyncio.run(get_queued_order(order_ref=order_ref, order_service=self.service))
        self.assertEqual(pending.status_code, 202)
        self.assertEqual(json.loads(pending.body.decode())["status"], "PENDING")

        self._deliver(_queued_message(order_ref))

        created = asyncio.run(get_queued_order(order_ref=order_ref, order_service=self.service))
        self.assertEqual(created.status_code, 200)
        data = json.loads(created.body.decode())
        self.assertEqual(data["status"], "CREATED")
        self.assertEqual(data["order_ref"], order_ref)
        self.assertEqual(data["order_id"], self.service.get_queued_order(order_ref)["order_id"])


if __name__ == "__main__":
    unittest.main()
