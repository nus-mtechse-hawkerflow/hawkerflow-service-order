from collections import defaultdict

from sqlalchemy import Engine
from sqlmodel import Session, select

from entities.order import Order
from entities.order_item import OrderItem
from models.order_details import OrderDetails


class OrderRepo:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create_order(self, order_details: OrderDetails):
        order = Order(
            f_total_price=order_details.total_price
        )

        for o in order_details.orders:
            for d in o.dishes:
                order_item = OrderItem(
                    f_stall_id=o.stall_id,
                    f_dish_id=d.dish_id,
                    f_quantity=d.quantity,
                    f_price=d.price
                )

                order.order_items.append(order_item)

        with Session(self._engine) as session:
            session.add(order)
            session.commit()
            session.refresh(order)
            return {
                "order_id": order.f_id,
                "total_price": order.f_total_price,
                "order_status": order.f_status,
                "order_created_at": order.f_created_at.strftime("%Y-%m-%d %H:%M:%S")
            }

    def get_order(self, order_id: int):
        statement = select(Order).where(Order.f_id == order_id)

        with (Session(self._engine) as session):
            order = session.exec(statement).first()
            order_details = {}

            if order is not None:
                order_details["order_id"] = order.f_id
                order_details["orders"] = self._populate_order_details(order.order_items)
                order_details["total_order_price"] = order.f_total_price
                order_details["order_created_at"] = order.f_created_at.strftime("%Y-%m-%d %H:%M:%S")

            return order_details

    def update_order(self, order_id: int, status: str):
        statement = select(Order).where(Order.f_id == order_id)

        with Session(self._engine) as session:
            order = session.exec(statement).first()
            order.f_status = status
            session.commit()
            session.refresh(order)

            return order

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
