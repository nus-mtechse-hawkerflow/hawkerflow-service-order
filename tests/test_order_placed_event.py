import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from configurations.app_config import EventsConfig, SqsConfig
from endpoints.order_routes import submit_order
from models.order_details import Dish, OrderDetails
from models.order_details import Order as OrderDto
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher
from services.order_service import OrderService
from workers.sqs_worker import SqsWorker


def _two_stall_order() -> OrderDetails:
    return OrderDetails(
        orders=[
            OrderDto(
                stall_id=101,
                dishes=[Dish(dish_id=1, dish_name="Chicken Rice", quantity=2, price=4.50)],
            ),
            OrderDto(stall_id=202, dishes=[Dish(dish_id=2, dish_name="Kopi", quantity=1, price=1.80)]),
        ],
        total_price=10.80,
    )


class TestOrderPlacedEvent(unittest.TestCase):
    """
    Every new order announces itself on the notification topic, so the event
    stream reads Placed -> Preparing -> Ready -> Completed.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.publisher = MagicMock(spec=EventPublisher)
        self.publisher.publish_order_placed = AsyncMock()
        self.service = OrderService(OrderRepo(self.engine), event_publisher=self.publisher)

    def _published(self) -> list[dict]:
        return [c.args[0] for c in self.publisher.publish_order_placed.call_args_list]

    def test_placing_an_order_publishes_order_placed_for_each_stall(self):
        placed = asyncio.run(self.service.place_order(_two_stall_order()))

        events = sorted(self._published(), key=lambda e: e["stall_id"])
        self.assertEqual([e["stall_id"] for e in events], [101, 202])
        for event in events:
            self.assertEqual(event["order_id"], placed["order_id"])
            self.assertEqual(event["status"], "PENDING")
            self.assertIn("stall_order_id", event)
        self.assertEqual([e["subtotal"] for e in events], [9.00, 1.80])

    def test_direct_order_endpoint_publishes_order_placed(self):
        asyncio.run(submit_order(orders=_two_stall_order(), order_service=self.service))

        self.assertEqual(len(self._published()), 2)

    def test_queued_order_publishes_order_placed_once_even_if_redelivered(self):
        worker = SqsWorker(
            SqsConfig(
                enabled=True,
                queue_url="https://sqs.local/000000000000/order_queue",
                region_name="ap-southeast-1",
            ),
            self.service,
            sqs_client=MagicMock(),
        )
        body = json.dumps({
            "event_type": "ORDER_PLACED",
            "order_ref": "ref-1",
            "data": _two_stall_order().model_dump(),
        })

        for message_id in ("msg-1", "msg-1-redelivered"):
            message = {"MessageId": message_id, "ReceiptHandle": message_id, "Body": body}
            asyncio.run(worker._handle_message(message))

        self.assertEqual(len(self._published()), 2)  # one per stall, from the first delivery only

    def test_a_failed_publish_does_not_fail_the_order(self):
        self.publisher.publish_order_placed.side_effect = RuntimeError("SNS unreachable")

        placed = asyncio.run(self.service.place_order(_two_stall_order()))

        self.assertIsNotNone(self.service.get_order(placed["order_id"]))


class TestPublishOrderPlaced(unittest.TestCase):
    def test_publishes_an_order_placed_envelope(self):
        client = MagicMock()
        publisher = EventPublisher(
            EventsConfig(enabled=True, topic_arn="arn:aws:sns:ap-southeast-1:000000000000:order_status"),
            client=client,
        )
        stall_order = {
            "stall_order_id": 7, "order_id": 110, "stall_id": 1, "status": "PENDING", "subtotal": 9.5,
        }

        published = asyncio.run(publisher.publish_order_placed(stall_order))

        self.assertEqual(published["event_type"], "OrderPlaced")
        self.assertEqual(published["data"], stall_order)
        attributes = client.publish.call_args.kwargs["MessageAttributes"]
        self.assertEqual(attributes["event_type"]["StringValue"], "OrderPlaced")
        self.assertEqual(attributes["stall_id"]["StringValue"], "1")


if __name__ == "__main__":
    unittest.main()
