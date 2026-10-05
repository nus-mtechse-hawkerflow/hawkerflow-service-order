import asyncio
import logging
from datetime import datetime, timedelta, timezone

from configurations.app_config import ExpiryConfig
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher

logger = logging.getLogger("hawkerflow-order.order_expiry")

# Singapore is UTC+8 all year, so no time zone database is needed
SINGAPORE_OFFSET = timedelta(hours=8)

# Why the worker, not a person, changed the order; sent with the event
EXPIRY_REASONS = {
    "CANCELLED": "NOT_ACCEPTED_IN_TIME",
    "COMPLETED": "NOT_COLLECTED_BY_DAY_END",
}


class OrderExpiryWorker:
    """
    Background worker that tidies up orders nobody is acting on:

    - a stall order still PENDING after `pending_minutes` is cancelled, so the
      diner is not left waiting on a stall that never accepted it;
    - a stall order still READY from an earlier Singapore day is completed,
      because the food was made and is a sale even if nobody collected it.

    Orders a stall has accepted or is preparing are never touched. Each change
    is announced as an OrderCancelled or OrderCompleted event when a publisher
    is given.
    """

    def __init__(
        self,
        config: ExpiryConfig,
        repo: OrderRepo,
        event_publisher: EventPublisher | None = None,
    ):
        self.config = config
        self._repo = repo
        self._publisher = event_publisher
        self._stop_event = asyncio.Event()

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """
        Applies both rules once, without announcing anything. `now` is naive
        UTC, matching how the order service stores f_created_at.
        """
        return self._expire(now)[0]

    async def run_and_publish(self, now: datetime | None = None) -> dict[str, int]:
        """Applies both rules once, then publishes an event for each stall order changed."""
        counts, stall_orders = await asyncio.to_thread(self._expire, now)

        if self._publisher:
            for stall_order in stall_orders:
                event = {**stall_order, "reason": EXPIRY_REASONS[stall_order["status"]]}
                try:
                    await self._publisher.publish_order_status_updated(event)
                except Exception as e:
                    logger.error(
                        "Failed to publish expiry event for order %s, stall %s: %s",
                        stall_order["order_id"],
                        stall_order["stall_id"],
                        e,
                    )

        return counts

    def _expire(self, now: datetime | None) -> tuple[dict[str, int], list[dict]]:
        if now is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)

        pending_before = now - timedelta(minutes=self.config.pending_minutes)

        ready_before = None
        if self.config.complete_ready_at_day_end:
            singapore_now = now + SINGAPORE_OFFSET
            singapore_midnight = singapore_now.replace(hour=0, minute=0, second=0, microsecond=0)
            ready_before = singapore_midnight - SINGAPORE_OFFSET

        result = self._repo.expire_stale_stall_orders(pending_before, ready_before)
        counts = {"cancelled": result["cancelled"], "completed": result["completed"]}
        if counts["cancelled"] or counts["completed"]:
            logger.info(
                "Order expiry: cancelled %d unaccepted, completed %d uncollected",
                counts["cancelled"],
                counts["completed"],
            )
        return counts, result["stall_orders"]

    async def start(self) -> None:
        """Runs the rules every `interval_seconds` until stop() is called or the task is cancelled."""
        logger.info(
            "Order expiry worker started (pending limit: %d min, every %ds)",
            self.config.pending_minutes,
            self.config.interval_seconds,
        )
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                await self.run_and_publish()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Order expiry run failed: %s. Retrying next interval.", e)

            try:
                await asyncio.sleep(self.config.interval_seconds)
            except asyncio.CancelledError:
                break

        logger.info("Order expiry worker stopped.")

    def stop(self) -> None:
        self._stop_event.set()
