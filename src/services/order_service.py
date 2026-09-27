import asyncio
import logging

from models.order_details import OrderDetails
from models.order_update import OrderUpdate
from repository.order_repo import OrderRepo
from services.event_publisher import EventPublisher


logger = logging.getLogger("hawkerflow-order.order_service")


class OrderService:
    def __init__(self, repo: OrderRepo, event_publisher: EventPublisher | None = None):
        self._repo = repo
        self._publisher = event_publisher

    def submit_order(self, order: OrderDetails, order_ref: str | None = None):
        return self._repo.create_order(order, order_ref=order_ref)

    async def place_order(self, order: OrderDetails, order_ref: str | None = None):
        """
        Create an order and publish OrderPlaced for each stall sub-order. A
        queued order whose order_ref was seen before is a redelivery: its order
        exists and was announced already, so nothing is published again.
        """
        is_redelivery = order_ref is not None and self._repo.get_order_id_by_ref(order_ref) is not None
        placed = self.submit_order(order, order_ref=order_ref)

        if self._publisher and not is_redelivery:
            for stall_order in self._repo.get_stall_orders(placed["order_id"]):
                try:
                    await self._publisher.publish_order_placed(stall_order)
                except Exception as e:
                    logger.error("Failed to publish OrderPlaced event: %s", e)

        return placed

    def get_order(self, order_id: int):
        return self._repo.get_order(order_id)

    def get_queued_order(self, order_ref: str) -> dict | None:
        """The order created from a queued order_ref, or None while it is still queued."""
        order_id = self._repo.get_order_id_by_ref(order_ref)
        if order_id is None:
            return None
        return {"order_ref": order_ref, "order_id": order_id}

    def update_order(self, order_update: OrderUpdate):
        return self._repo.update_order(order_update.order_id, order_update.status)

    def get_orders_for_stall(self, stall_id: int, status: str | None = None) -> list[dict]:
        """Fetch orders belonging strictly to the specified stall."""
        return self._repo.get_orders_for_stall(stall_id, status)

    async def update_stall_order_status(self, stall_id: int, order_id: int, status: str) -> dict | None:
        """
        Update the preparation status of an order for a specific stall.
        Automatically publishes the corresponding domain event (e.g. OrderAccepted, OrderReady).
        """
        result = self._repo.update_stall_order_status(stall_id, order_id, status)

        if result and self._publisher:
            try:
                if status.upper() == "READY":
                    await self._publisher.publish_order_ready(result)
                else:
                    await self._publisher.publish_order_status_updated(result)
            except Exception as e:
                logger.error("Failed to publish Order %s event: %s", status, e)

        return result

    async def process_incoming_sqs_message(self, payload: dict):
        """
        Process an order message received from SQS.
        Supports both wrapped event format ({"event_type": "...", "data": {...}})
        and raw OrderDetails payload.
        """
        logger.info("Processing incoming SQS message payload: %s", payload)
        event_type = payload.get("event_type", "ORDER_PLACED")

        match event_type:
            case "ORDER_PLACE" | "ORDER_PLACED":
                order_data = payload.get("data", payload)
                order_details = OrderDetails(**order_data)
                result = await self.place_order(order_details, order_ref=payload.get("order_ref"))

                logger.info("Successfully created order via SQS: %s", result.get("order_id"))
                return result

            case "ORDER_UPDATED":
                update_data = payload.get("data", payload)
                stall_id = update_data.get("stall_id")
                order_id = update_data.get("order_id")
                status = update_data.get("status")

                if stall_id is not None and order_id is not None and status:
                    return await self.update_stall_order_status(int(stall_id), int(order_id), status)

                elif order_id is not None and status:
                    return self.update_order(OrderUpdate(order_id=int(order_id), status=status))

                else:
                    raise ValueError("Invalid update payload: missing order_id or status")

            case _:
                # Fallback: parse as direct OrderDetails
                order_details = OrderDetails(**payload)
                return await self.place_order(order_details)
