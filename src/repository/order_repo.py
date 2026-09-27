from collections import defaultdict
from sqlalchemy import Engine
from sqlmodel import Session, select

from entities.order import Order
from entities.order_item import OrderItem
from entities.order_option import OrderOption
from entities.order_request import OrderRequest
from entities.stall_order import StallOrder
from models.order_details import OrderDetails


class OrderRepo:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create_order(self, order_details: OrderDetails, order_ref: str | None = None):
        if order_ref:
            # SQS delivers at least once. A ref already recorded means this
            # message was processed before: return that order, don't add another.
            # Two deliveries racing past this check still cannot both commit,
            # because order_ref is the primary key of order_requests.
            with Session(self._engine) as session:
                existing = session.get(OrderRequest, order_ref)
                if existing:
                    return self._order_summary(session.get(Order, existing.f_order_id))

        order = Order(
            f_total_price=order_details.total_price,
            f_status="PENDING"
        )

        for o in order_details.orders:
            subtotal = sum(d.price * d.quantity for d in o.dishes)
            stall_order = StallOrder(
                f_stall_id=o.stall_id,
                f_status="PENDING",
                f_subtotal=subtotal
            )
            order.stall_orders.append(stall_order)

            for d in o.dishes:
                order_item = OrderItem(
                    f_stall_id=o.stall_id,
                    f_dish_id=d.dish_id,
                    f_dish_name=d.dish_name,
                    f_quantity=d.quantity,
                    f_price=d.price,
                    stall_order=stall_order
                )
                order.order_items.append(order_item)

        with Session(self._engine) as session:
            session.add(order)
            # Same transaction as the order, so these never point at a
            # half-written order.
            session.flush()
            session.add(OrderOption(
                f_order_id=order.f_id,
                f_dining_option=order_details.dining_option,
                f_takeaway_fee=order_details.takeaway_fee,
            ))
            if order_ref:
                session.add(OrderRequest(f_order_ref=order_ref, f_order_id=order.f_id))
            session.commit()
            session.refresh(order)
            return self._order_summary(order)

    def get_stall_orders(self, order_id: int) -> list[dict]:
        """An order's stall sub-orders, shaped like update_stall_order_status's result."""
        statement = select(StallOrder).where(StallOrder.f_order_id == order_id)
        with Session(self._engine) as session:
            return [
                {
                    "stall_order_id": so.f_id,
                    "order_id": so.f_order_id,
                    "stall_id": so.f_stall_id,
                    "status": so.f_status,
                    "subtotal": so.f_subtotal
                }
                for so in session.exec(statement).all()
            ]

    def get_order_id_by_ref(self, order_ref: str) -> int | None:
        with Session(self._engine) as session:
            request = session.get(OrderRequest, order_ref)
            return request.f_order_id if request else None

    @staticmethod
    def _order_summary(order: Order) -> dict:
        return {
            "order_id": order.f_id,
            "total_price": order.f_total_price,
            "order_status": order.f_status,
            "order_created_at": order.f_created_at.strftime("%Y-%m-%d %H:%M:%S")
        }

    def get_order(self, order_id: int):
        statement = select(Order).where(Order.f_id == order_id)

        with Session(self._engine) as session:
            order = session.exec(statement).first()
            order_details = {}

            if order is not None:
                order_details["order_id"] = order.f_id
                order_details["orders"] = self._populate_order_details(order.order_items)
                order_details["total_order_price"] = order.f_total_price
                order_details["order_status"] = order.f_status
                order_details["order_created_at"] = order.f_created_at.strftime("%Y-%m-%d %H:%M:%S")
                option = session.get(OrderOption, order.f_id)
                order_details["dining_option"] = option.f_dining_option if option else "dine_in"
                order_details["takeaway_fee"] = option.f_takeaway_fee if option else 0.0

            return order_details

    def update_order(self, order_id: int, status: str):
        statement = select(Order).where(Order.f_id == order_id)

        with Session(self._engine) as session:
            order = session.exec(statement).first()
            if order:
                order.f_status = status
                session.commit()
                session.refresh(order)
            return order

    def get_orders_for_stall(self, stall_id: int, status: str | None = None) -> list[dict]:
        """Fetch orders belonging exclusively to the specified stall."""
        statement = select(StallOrder).where(StallOrder.f_stall_id == stall_id)
        if status:
            statement = statement.where(StallOrder.f_status == status.upper())

        with Session(self._engine) as session:
            stall_orders = session.exec(statement).all()
            order_ids = {so.f_order_id for so in stall_orders}
            options = {
                o.f_order_id: o.f_dining_option
                for o in session.exec(select(OrderOption).where(OrderOption.f_order_id.in_(order_ids)))
            } if order_ids else {}
            results = []
            for so in stall_orders:
                results.append({
                    "stall_order_id": so.f_id,
                    "order_id": so.f_order_id,
                    "stall_id": so.f_stall_id,
                    "status": so.f_status,
                    "subtotal": so.f_subtotal,
                    "dining_option": options.get(so.f_order_id, "dine_in"),
                    "created_at": so.order.f_created_at.strftime("%Y-%m-%d %H:%M:%S") if so.order else None,
                    "items": [
                        {
                            "dish_id": item.f_dish_id,
                            "quantity": item.f_quantity,
                            "price": item.f_price
                        }
                        for item in so.items
                    ]
                })
            return results

    def update_stall_order_status(self, stall_id: int, order_id: int, status: str) -> dict | None:
        """Update the order status for a specific stall and sync the parent order if applicable."""
        statement = select(StallOrder).where(
            StallOrder.f_stall_id == stall_id,
            StallOrder.f_order_id == order_id
        )

        with Session(self._engine) as session:
            stall_order = session.exec(statement).first()
            if not stall_order:
                return None

            stall_order.f_status = status
            session.add(stall_order)

            # Check all sibling stall orders for the parent order to sync parent status
            sibling_statement = select(StallOrder).where(StallOrder.f_order_id == order_id)
            all_stall_orders = session.exec(sibling_statement).all()

            order_statement = select(Order).where(Order.f_id == order_id)
            parent_order = session.exec(order_statement).first()

            if parent_order:
                statuses = [so.f_status for so in all_stall_orders]
                if all(s == "COMPLETED" for s in statuses):
                    parent_order.f_status = "COMPLETED"
                elif all(s == "READY" for s in statuses):
                    parent_order.f_status = "READY"
                elif any(s in ("PREPARING", "READY", "ACCEPTED") for s in statuses):
                    parent_order.f_status = "IN_PROGRESS"
                session.add(parent_order)

            session.commit()
            session.refresh(stall_order)

            return {
                "stall_order_id": stall_order.f_id,
                "order_id": stall_order.f_order_id,
                "stall_id": stall_order.f_stall_id,
                "status": stall_order.f_status,
                "subtotal": stall_order.f_subtotal
            }

    def _populate_order_details(self, order_items: list[OrderItem]):
        grouped = defaultdict(list)

        for item in order_items:
            stall_id = item.f_stall_id

            grouped[stall_id].append(
                {
                    "dish_id": item.f_dish_id,
                    "quantity": item.f_quantity,
                    "price": item.f_price
                }
            )

        orders = [
            {
                "stall_id": stall_id,
                "dishes": dishes
            }
            for stall_id, dishes in grouped.items()
        ]

        return orders
