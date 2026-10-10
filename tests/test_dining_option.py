import asyncio
import json
import unittest
from unittest.mock import MagicMock

from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from configurations.app_config import SqsConfig
from entities.order import Order
from models.order_details import Dish, OrderDetails
from models.order_details import Order as OrderDto
from repository.order_repo import OrderRepo
from services.order_service import OrderService
from workers.sqs_worker import SqsWorker


def _order(**options) -> OrderDetails:
    return OrderDetails(
        orders=[
            OrderDto(stall_id=1, dishes=[Dish(dish_id=1, dish_name="Chicken Rice", quantity=1, price=4.50)]),
        ],
        total_price=4.80 if options.get("dining_option") == "takeaway" else 4.50,
        **options,
    )


class TestDiningOption(unittest.TestCase):
    """
    Dine-in and takeaway are both self-collect; the stall needs to know which
    orders to pack, and the takeaway fee is recorded rather than hidden in the total.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.service = OrderService(OrderRepo(self.engine))

    def test_takeaway_order_is_flagged_to_the_stall_with_its_fee(self):
        placed = self.service.submit_order(_order(dining_option="takeaway", takeaway_fee=0.30))

        order = self.service.get_order(placed["order_id"])
        self.assertEqual(order["dining_option"], "takeaway")
        self.assertEqual(order["takeaway_fee"], 0.30)
        self.assertEqual(self.service.get_orders_for_stall(1)[0]["dining_option"], "takeaway")

    def test_order_without_a_dining_option_is_dine_in(self):
        """The hawker POS does not send a dining option."""
        placed = self.service.submit_order(_order())

        order = self.service.get_order(placed["order_id"])
        self.assertEqual(order["dining_option"], "dine_in")
        self.assertEqual(order["takeaway_fee"], 0.0)
        self.assertEqual(self.service.get_orders_for_stall(1)[0]["dining_option"], "dine_in")

    def test_orders_created_before_dining_options_read_as_dine_in(self):
        with Session(self.engine) as session:
            session.add(Order(f_total_price=4.50))
            session.commit()

        self.assertEqual(self.service.get_order(1)["dining_option"], "dine_in")

    def test_queued_order_keeps_its_dining_option(self):
        config = SqsConfig(
            enabled=True,
            queue_url="https://sqs.local/000000000000/order_queue",
            region_name="ap-southeast-1",
        )
        order_ref = "ref-takeaway"
        # The message as diner-ui posts it and API Gateway queues it
        body = json.dumps({
            "event_type": "ORDER_PLACED",
            "order_ref": order_ref,
            "data": _order(dining_option="takeaway", takeaway_fee=0.30).model_dump(),
        })
        worker = SqsWorker(config, self.service, sqs_client=MagicMock())

        asyncio.run(worker._handle_message({"MessageId": "m1", "ReceiptHandle": "r1", "Body": body}))

        order_id = self.service.get_queued_order(order_ref)["order_id"]
        self.assertEqual(self.service.get_order(order_id)["dining_option"], "takeaway")
        self.assertEqual(json.loads(body)["data"]["takeaway_fee"], 0.30)

    def test_unknown_dining_option_is_rejected(self):
        with self.assertRaises(ValidationError):
            _order(dining_option="delivery")

    def test_negative_takeaway_fee_is_rejected(self):
        with self.assertRaises(ValidationError):
            _order(dining_option="takeaway", takeaway_fee=-0.30)


if __name__ == "__main__":
    unittest.main()
