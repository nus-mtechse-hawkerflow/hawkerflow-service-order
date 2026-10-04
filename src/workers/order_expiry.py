import asyncio
import logging
from datetime import datetime, timedelta, timezone

from configurations.app_config import ExpiryConfig
from repository.order_repo import OrderRepo

logger = logging.getLogger("hawkerflow-order.order_expiry")

# Singapore is UTC+8 all year, so no time zone database is needed
SINGAPORE_OFFSET = timedelta(hours=8)


class OrderExpiryWorker:
    """
    Background worker that tidies up orders nobody is acting on:

    - a stall order still PENDING after `pending_minutes` is cancelled, so the
      diner is not left waiting on a stall that never accepted it;
    - a stall order still READY from an earlier Singapore day is completed,
      because the food was made and is a sale even if nobody collected it.

    Orders a stall has accepted or is preparing are never touched.
    """

    def __init__(self, config: ExpiryConfig, repo: OrderRepo):
        self.config = config
        self._repo = repo
        self._stop_event = asyncio.Event()

    def run_once(self, now: datetime | None = None) -> dict[str, int]:
        """
        Applies both rules once. `now` is naive UTC, matching how the order
        service stores f_created_at.
        """
        if now is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)

        pending_before = now - timedelta(minutes=self.config.pending_minutes)

        ready_before = None
        if self.config.complete_ready_at_day_end:
            singapore_now = now + SINGAPORE_OFFSET
            singapore_midnight = singapore_now.replace(hour=0, minute=0, second=0, microsecond=0)
            ready_before = singapore_midnight - SINGAPORE_OFFSET

        result = self._repo.expire_stale_stall_orders(pending_before, ready_before)
        if result["cancelled"] or result["completed"]:
            logger.info(
                "Order expiry: cancelled %d unaccepted, completed %d uncollected",
                result["cancelled"],
                result["completed"],
            )
        return result

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
                await asyncio.to_thread(self.run_once)
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
