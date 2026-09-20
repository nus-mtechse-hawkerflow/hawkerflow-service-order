import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from sqlmodel import SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from configurations.app_config import EventsConfig
from models.order_details import Dish, Order as OrderDto, OrderDetails
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher
from services.order_service import OrderService


class TestEventPublisher(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.repo = OrderRepo(self.engine)

    def test_publish_order_ready_sqs(self):
        """
        Verify that publishing an OrderReady event to SQS constructs the correct
        envelope and sends message to the configured queue URL.
        """
        mock_sqs = MagicMock()
        config = EventsConfig(
            enabled=True,
            topic_arn=None,
            notification_queue_url="https://sqs.us-east-1.amazonaws.com/123/notifications",
            region_name="us-east-1",
        )
        publisher = EventPublisher(config, client=mock_sqs)

        stall_order_data = {
            "stall_order_id": 1,
            "order_id": 10,
            "stall_id": 101,
            "status": "READY",
            "subtotal": 12.50,
        }

        published = asyncio.run(publisher.publish_order_ready(stall_order_data))

        self.assertIsNotNone(published)
        self.assertEqual(published["event_type"], "OrderReady")
        self.assertIn("event_id", published)
        self.assertIn("timestamp", published)
        self.assertEqual(published["data"], stall_order_data)

        # Verify mock_sqs.send_message was called
        mock_sqs.send_message.assert_called_once()
        call_kwargs = mock_sqs.send_message.call_args[1]
        self.assertEqual(call_kwargs["QueueUrl"], config.notification_queue_url)
        body = json.loads(call_kwargs["MessageBody"])
        self.assertEqual(body["event_type"], "OrderReady")
        self.assertEqual(body["data"]["status"], "READY")

    def test_publish_order_ready_sns(self):
        """
        Verify that publishing an OrderReady event to SNS publishes to the topic
        with message attributes for event_type and stall_id.
        """
        mock_sns = MagicMock()
        config = EventsConfig(
            enabled=True,
            topic_arn="arn:aws:sns:us-east-1:123:hawkerflow-order-events",
            region_name="us-east-1",
        )
        publisher = EventPublisher(config, client=mock_sns)

        stall_order_data = {
            "stall_order_id": 2,
            "order_id": 20,
            "stall_id": 202,
            "status": "READY",
            "subtotal": 18.00,
        }

        published = asyncio.run(publisher.publish_order_ready(stall_order_data))
        self.assertIsNotNone(published)

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        self.assertEqual(call_kwargs["TopicArn"], config.topic_arn)
        attrs = call_kwargs["MessageAttributes"]
        self.assertEqual(attrs["event_type"]["StringValue"], "OrderReady")
        self.assertEqual(attrs["stall_id"]["StringValue"], "202")

    def test_order_service_triggers_publish_only_on_ready(self):
        """
        Verify that OrderService only triggers publish_order_ready when status is READY,
        and not for other statuses like PREPARING or ACCEPTED.
        """
        mock_publisher = MagicMock(spec=EventPublisher)
        mock_publisher.publish_order_ready = AsyncMock()

        service = OrderService(self.repo, event_publisher=mock_publisher)

        # Create an order
        order_payload = OrderDetails(
            orders=[
                OrderDto(stall_id=101, dishes=[Dish(dish_id=1, quantity=1, price=5.0)]),
            ],
            total_price=5.0,
        )
        placed = service.submit_order(order_payload)
        order_id = placed["order_id"]

        # 1. Update status to PREPARING -> Should NOT trigger publish_order_ready
        asyncio.run(service.update_stall_order_status(stall_id=101, order_id=order_id, status="PREPARING"))
        mock_publisher.publish_order_ready.assert_not_called()

        # 2. Update status to READY -> MUST trigger publish_order_ready!
        asyncio.run(service.update_stall_order_status(stall_id=101, order_id=order_id, status="READY"))
        mock_publisher.publish_order_ready.assert_called_once()
        called_arg = mock_publisher.publish_order_ready.call_args[0][0]
        self.assertEqual(called_arg["status"], "READY")
        self.assertEqual(called_arg["stall_id"], 101)
        self.assertEqual(called_arg["order_id"], order_id)

    def test_disabled_publisher_does_not_call_aws(self):
        """
        When config.enabled is False, publish_event returns None without calling AWS.
        """
        mock_sqs = MagicMock()
        config = EventsConfig(enabled=False, notification_queue_url="https://fake.url")
        publisher = EventPublisher(config, client=mock_sqs)

        res = asyncio.run(publisher.publish_order_ready({"order_id": 1, "status": "READY"}))
        self.assertIsNone(res)
        mock_sqs.send_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
