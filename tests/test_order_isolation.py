import asyncio
import unittest
from fastapi import HTTPException
from sqlmodel import SQLModel, create_engine

from dependencies.auth import get_current_stall_id, verify_stall_access
from endpoints.order_routes import (
    get_my_stall_orders,
    get_order,
    get_stall_orders,
    submit_order,
    update_stall_order_status,
)
from entities import Order, OrderItem, StallOrder
from models.order_details import Dish, Order as OrderDto, OrderDetails
from models.stall_order_update import StallOrderUpdate
from repository.order_repo import OrderRepo
from services.order_service import OrderService


class TestOrderIsolation(unittest.TestCase):
    def setUp(self):
        # Set up an in-memory SQLite database
        self.engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(self.engine)

        self.repo = OrderRepo(self.engine)
        self.service = OrderService(self.repo)

    def test_multi_stall_order_creation_and_isolation(self):
        """
        Diner places an order spanning Stall 101 and Stall 202.
        Verify each stall only retrieves and sees their own items and subtotal.
        """
        payload = OrderDetails(
            orders=[
                OrderDto(
                    stall_id=101,
                    dishes=[
                        Dish(dish_id=1, quantity=2, price=5.50),  # $11.00
                        Dish(dish_id=2, quantity=1, price=4.00),  # $4.00 -> total 101 = $15.00
                    ],
                ),
                OrderDto(
                    stall_id=202,
                    dishes=[
                        Dish(dish_id=3, quantity=1, price=8.00),  # $8.00 -> total 202 = $8.00
                    ],
                ),
            ],
            total_price=23.00,
        )

        placed = self.service.submit_order(payload)
        order_id = placed["order_id"]
        self.assertIsNotNone(order_id)

        # Stall 101 fetches their orders
        stall_101_orders = self.service.get_orders_for_stall(101)
        self.assertEqual(len(stall_101_orders), 1)
        self.assertEqual(stall_101_orders[0]["stall_id"], 101)
        self.assertEqual(stall_101_orders[0]["subtotal"], 15.00)
        self.assertEqual(len(stall_101_orders[0]["items"]), 2)
        dish_ids_101 = [item["dish_id"] for item in stall_101_orders[0]["items"]]
        self.assertIn(1, dish_ids_101)
        self.assertIn(2, dish_ids_101)
        self.assertNotIn(3, dish_ids_101)  # Stall 202's dish must NOT be present!

        # Stall 202 fetches their orders
        stall_202_orders = self.service.get_orders_for_stall(202)
        self.assertEqual(len(stall_202_orders), 1)
        self.assertEqual(stall_202_orders[0]["stall_id"], 202)
        self.assertEqual(stall_202_orders[0]["subtotal"], 8.00)
        self.assertEqual(len(stall_202_orders[0]["items"]), 1)
        self.assertEqual(stall_202_orders[0]["items"][0]["dish_id"], 3)

        # A stall that was not part of the order should see zero orders
        stall_303_orders = self.service.get_orders_for_stall(303)
        self.assertEqual(len(stall_303_orders), 0)

    def test_stall_status_updates_are_independent(self):
        """
        When Stall 101 updates their status to PREPARING, Stall 202's status stays PENDING.
        """
        payload = OrderDetails(
            orders=[
                OrderDto(stall_id=101, dishes=[Dish(dish_id=1, quantity=1, price=5.0)]),
                OrderDto(stall_id=202, dishes=[Dish(dish_id=2, quantity=1, price=6.0)]),
            ],
            total_price=11.0,
        )
        placed = self.service.submit_order(payload)
        order_id = placed["order_id"]

        # Stall 101 updates their status
        res = asyncio.run(self.service.update_stall_order_status(stall_id=101, order_id=order_id, status="PREPARING"))
        self.assertEqual(res["status"], "PREPARING")

        # Verify Stall 202's status is still PENDING
        stall_202_orders = self.service.get_orders_for_stall(202)
        self.assertEqual(stall_202_orders[0]["status"], "PENDING")

        # Parent order should be IN_PROGRESS
        overall_order = self.service.get_order(order_id)
        self.assertEqual(overall_order["order_status"], "IN_PROGRESS")

        # Now Stall 202 completes, and Stall 101 completes
        asyncio.run(self.service.update_stall_order_status(stall_id=101, order_id=order_id, status="COMPLETED"))
        asyncio.run(self.service.update_stall_order_status(stall_id=202, order_id=order_id, status="COMPLETED"))

        # Parent order should now be COMPLETED
        overall_order = self.service.get_order(order_id)
        self.assertEqual(overall_order["order_status"], "COMPLETED")

    def test_auth_dependency_and_access_control(self):
        """
        Verify that auth dependency extracts stall_id from X-Stall-ID and Bearer token,
        and verify_stall_access blocks cross-tenant access with 403 Forbidden.
        """
        # Test X-Stall-ID header
        stall_id = asyncio.run(get_current_stall_id(x_stall_id="101"))
        self.assertEqual(stall_id, 101)

        # Test missing auth raises 401
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_current_stall_id())
        self.assertEqual(ctx.exception.status_code, 401)

        # Test cross-tenant access rejection
        # Authorized as 101, attempting to access 202 -> 403
        with self.assertRaises(HTTPException) as ctx:
            verify_stall_access(requested_stall_id=202, authenticated_stall_id=101)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("not authorized", ctx.exception.detail)

        # Legitimate access -> does not raise
        verify_stall_access(requested_stall_id=101, authenticated_stall_id=101)

    def test_endpoint_handlers(self):
        """
        Test route handlers for stall orders and status updates directly.
        """
        payload = OrderDetails(
            orders=[
                OrderDto(stall_id=101, dishes=[Dish(dish_id=1, quantity=1, price=7.5)]),
            ],
            total_price=7.5,
        )
        placed = self.service.submit_order(payload)
        order_id = placed["order_id"]

        # Fetch stall orders via route handler
        response = asyncio.run(
            get_stall_orders(
                stall_id=101,
                current_stall_id=101,
                order_service=self.service,
            )
        )
        import json
        data = json.loads(response.body.decode())
        self.assertEqual(data["stall_id"], 101)
        self.assertEqual(len(data["orders"]), 1)

        # Cross-stall access attempt via route handler raises 403
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                get_stall_orders(
                    stall_id=101,
                    current_stall_id=202,
                    order_service=self.service,
                )
            )
        self.assertEqual(ctx.exception.status_code, 403)

        # Update status via route handler
        update_response = asyncio.run(
            update_stall_order_status(
                stall_id=101,
                order_id=order_id,
                body=StallOrderUpdate(status="READY"),
                current_stall_id=101,
                order_service=self.service,
            )
        )
        update_data = json.loads(update_response.body.decode())
        self.assertEqual(update_data["status"], "READY")


if __name__ == "__main__":
    unittest.main()
