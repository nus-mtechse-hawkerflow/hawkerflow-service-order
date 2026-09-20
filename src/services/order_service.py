import logging
from models.order_details import OrderDetails
from models.order_update import OrderUpdate
from repository.order_repo import OrderRepo

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self, repo: OrderRepo):
        self._repo = repo

    def submit_order(self, order: OrderDetails):
        return self._repo.create_order(order)

    def get_order(self, order_id: int):
        return self._repo.get_order(order_id)

    def update_order(self, order_update: OrderUpdate):
        return self._repo.update_order(order_update.order_id, order_update.status)

    def get_orders_for_stall(self, stall_id: int, status: str | None = None) -> list[dict]:
        """Fetch orders belonging strictly to the specified stall."""
        return self._repo.get_orders_for_stall(stall_id, status)

    def update_stall_order_status(self, stall_id: int, order_id: int, status: str) -> dict | None:
        """Update preparation status of an order for a specific stall."""
        return self._repo.update_stall_order_status(stall_id, order_id, status)

    def process_incoming_sqs_message(self, payload: dict):
        """
        Process an order message received from SQS.
        Supports both wrapped event format ({"event_type": "...", "data": {...}})
        and raw OrderDetails payload.
        """
        logger.info("Processing incoming SQS message payload: %s", payload)
        event_type = payload.get("event_type", "ORDER_PLACED")

        match event_type:
            case "ORDER_PLACE":
                order_data = payload.get("data", payload)
                order_details = OrderDetails(**order_data)
                result = self.submit_order(order_details)

                logger.info("Successfully created order via SQS: %s", result.get("order_id"))
                return result

            case "ORDER_UPDATED":
                update_data = payload.get("data", payload)
                stall_id = update_data.get("stall_id")
                order_id = update_data.get("order_id")
                status = update_data.get("status")

                if stall_id is not None and order_id is not None and status:
                    return self.update_stall_order_status(int(stall_id), int(order_id), status)

                elif order_id is not None and status:
                    return self.update_order(OrderUpdate(order_id=int(order_id), status=status))

                else:
                    raise ValueError("Invalid update payload: missing order_id or status")

            case _:
                # Fallback: parse as direct OrderDetails
                order_details = OrderDetails(**payload)
                return self.submit_order(order_details)
