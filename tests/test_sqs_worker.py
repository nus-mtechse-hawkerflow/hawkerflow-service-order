import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from configurations.app_config import SqsConfig
from repository.order_repo import OrderRepo
from services.order_service import OrderService
from workers.sqs_worker import SqsWorker


class TestSqsWorker(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.repo = OrderRepo(self.engine)
        self.service = OrderService(self.repo)

        self.sqs_config = SqsConfig(
            enabled=True,
            queue_url="https://sqs.ap-southeast-1.amazonaws.com/123456789012/test-orders",
            region_name="ap-southeast-1",
            wait_time_seconds=1,
            max_number_of_messages=5,
            visibility_timeout=30,
        )

    def test_process_valid_order_message_deletes_from_queue(self):
        """
        When a valid SQS order message is received, it should be processed into the DB
        and deleted from the SQS queue.
        """
        mock_sqs = MagicMock()
        worker = SqsWorker(self.sqs_config, self.service, sqs_client=mock_sqs)

        order_payload = {
            "event_type": "ORDER_PLACED",
            "data": {
                "orders": [
                    {
                        "stall_id": 105,
                        "dishes": [{"dish_id": 10, "quantity": 2, "price": 4.50}],
                    }
                ],
                "total_price": 9.00,
            },
        }

        sqs_message = {
            "MessageId": "msg-12345",
            "ReceiptHandle": "receipt-abcde",
            "Body": json.dumps(order_payload),
        }

        # Run _handle_message
        asyncio.run(worker._handle_message(sqs_message))

        # Verify order was saved in database
        stall_orders = self.service.get_orders_for_stall(105)
        self.assertEqual(len(stall_orders), 1)
        self.assertEqual(stall_orders[0]["stall_id"], 105)
        self.assertEqual(stall_orders[0]["subtotal"], 9.00)

        # Verify delete_message was called with the receipt handle
        mock_sqs.delete_message.assert_called_once_with(
            QueueUrl=self.sqs_config.queue_url,
            ReceiptHandle="receipt-abcde",
        )

    def test_processing_failure_does_not_delete_message(self):
        """
        When processing a message fails with an unexpected exception, delete_message
        must NOT be called so that AWS SQS can retry and ultimately route to DLQ.
        """
        mock_sqs = MagicMock()
        mock_service = MagicMock(spec=OrderService)
        mock_service.process_incoming_sqs_message.side_effect = RuntimeError("Database down")

        worker = SqsWorker(self.sqs_config, mock_service, sqs_client=mock_sqs)

        sqs_message = {
            "MessageId": "msg-err",
            "ReceiptHandle": "receipt-err",
            "Body": json.dumps({"event_type": "ORDER_PLACED"}),
        }

        asyncio.run(worker._handle_message(sqs_message))

        # delete_message MUST NOT be called!
        mock_sqs.delete_message.assert_not_called()

    def test_invalid_json_does_not_crash_or_delete(self):
        """
        Poison message with non-JSON body should be caught safely without calling delete_message.
        """
        mock_sqs = MagicMock()
        worker = SqsWorker(self.sqs_config, self.service, sqs_client=mock_sqs)

        sqs_message = {
            "MessageId": "msg-poison",
            "ReceiptHandle": "receipt-poison",
            "Body": "not a valid json {{{",
        }

        asyncio.run(worker._handle_message(sqs_message))
        mock_sqs.delete_message.assert_not_called()

    def test_worker_stop_signal(self):
        """
        Verifies that calling worker.stop() sets the stop event.
        """
        mock_sqs = MagicMock()
        worker = SqsWorker(self.sqs_config, self.service, sqs_client=mock_sqs)
        self.assertFalse(worker._stop_event.is_set())

        worker.stop()
        self.assertTrue(worker._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
