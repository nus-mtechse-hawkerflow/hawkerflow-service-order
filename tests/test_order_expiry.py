import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from configurations.app_config import AppConfig, ExpiryConfig
from entities.order import Order
from entities.stall_order import StallOrder
from lifecycle.lifespan import create_expiry_worker
from models.order_details import Dish, OrderDetails
from models.order_details import Order as OrderDto
from repository.order_repo import OrderRepo
from workers.order_expiry import OrderExpiryWorker

REPO_ROOT = str(Path(__file__).resolve().parents[1])

# 4 October 2026, 14:00 in Singapore (06:00 UTC), as the naive UTC the database stores
NOW = datetime(2026, 10, 4, 6, 0, 0)


class TestOrderExpiry(unittest.TestCase):
    """
    A stall that never accepts an order must not leave the diner waiting
    forever, and food that was made but never collected is still a sale.
    """

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        self.repo = OrderRepo(self.engine)
        self.worker = OrderExpiryWorker(ExpiryConfig(enabled=True, pending_minutes=15), self.repo)

    def _order(self, age: timedelta, statuses: dict[int, str]) -> int:
        """Creates an order placed `age` ago with one sub-order per stall in the given status."""
        placed = self.repo.create_order(OrderDetails(
            orders=[
                OrderDto(stall_id=stall_id, dishes=[Dish(dish_id=1, dish_name="Dish", quantity=1, price=5.0)])
                for stall_id in statuses
            ],
            total_price=5.0 * len(statuses),
        ))
        order_id = placed["order_id"]
        with Session(self.engine) as session:
            order = session.get(Order, order_id)
            order.f_created_at = NOW - age
            session.add(order)
            session.commit()
        for stall_id, status in statuses.items():
            if status != "PENDING":
                self.repo.update_stall_order_status(stall_id, order_id, status)
        return order_id

    def _statuses(self, order_id: int) -> tuple[str, dict[int, str]]:
        with Session(self.engine) as session:
            parent = session.get(Order, order_id).f_status
            stalls = session.exec(select(StallOrder).where(StallOrder.f_order_id == order_id)).all()
            return parent, {so.f_stall_id: so.f_status for so in stalls}

    def test_pending_order_nobody_accepted_in_time_is_cancelled(self):
        order_id = self._order(timedelta(minutes=16), {1: "PENDING"})

        result = self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id), ("CANCELLED", {1: "CANCELLED"}))
        self.assertEqual(result, {"cancelled": 1, "completed": 0})

    def test_pending_order_within_the_limit_is_left_alone(self):
        order_id = self._order(timedelta(minutes=14), {1: "PENDING"})

        self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id), ("PENDING", {1: "PENDING"}))

    def test_orders_the_stall_is_working_on_are_never_expired(self):
        accepted = self._order(timedelta(hours=30), {1: "ACCEPTED"})
        preparing = self._order(timedelta(hours=30), {1: "PREPARING"})

        self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(accepted)[1], {1: "ACCEPTED"})
        self.assertEqual(self._statuses(preparing)[1], {1: "PREPARING"})

    def test_uncollected_ready_order_from_an_earlier_day_is_completed(self):
        # Placed at 22:00 Singapore time the previous day
        order_id = self._order(timedelta(hours=16), {1: "READY"})

        result = self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id), ("COMPLETED", {1: "COMPLETED"}))
        self.assertEqual(result, {"cancelled": 0, "completed": 1})

    def test_ready_order_from_today_keeps_waiting_for_collection(self):
        # Placed at 01:00 Singapore time today: an earlier UTC date, but the same Singapore day
        order_id = self._order(timedelta(hours=13), {1: "READY"})

        self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id)[1], {1: "READY"})

    def test_only_the_stall_that_ignored_the_order_is_cancelled(self):
        order_id = self._order(timedelta(minutes=20), {1: "PREPARING", 2: "PENDING"})

        self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id), ("IN_PROGRESS", {1: "PREPARING", 2: "CANCELLED"}))

    def test_order_is_complete_once_every_stall_has_finished_or_been_cancelled(self):
        order_id = self._order(timedelta(minutes=20), {1: "COMPLETED", 2: "PENDING"})

        self.worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id), ("COMPLETED", {1: "COMPLETED", 2: "CANCELLED"}))

    def test_day_end_completion_can_be_switched_off(self):
        worker = OrderExpiryWorker(
            ExpiryConfig(enabled=True, complete_ready_at_day_end=False), self.repo
        )
        order_id = self._order(timedelta(hours=16), {1: "READY"})

        worker.run_once(now=NOW)

        self.assertEqual(self._statuses(order_id)[1], {1: "READY"})


class TestExpiryConfiguration(unittest.TestCase):
    def _config(self, env: dict[str, str]) -> AppConfig:
        with patch.dict(os.environ, {"PROJECT_ROOT": REPO_ROOT, **env}):
            return AppConfig()

    def test_expiry_is_off_unless_a_deployment_turns_it_on(self):
        config = self._config({})

        self.assertFalse(config.expiry.enabled)
        self.assertIsNone(create_expiry_worker(config.expiry, repo=None))

    def test_environment_turns_expiry_on_and_sets_the_limit(self):
        config = self._config({"EXPIRY__ENABLED": "true", "EXPIRY__PENDING_MINUTES": "20"})

        self.assertTrue(config.expiry.enabled)
        self.assertEqual(config.expiry.pending_minutes, 20)
        self.assertIsNotNone(create_expiry_worker(config.expiry, repo=None))


if __name__ == "__main__":
    unittest.main()
