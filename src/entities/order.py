from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from entities.order_item import OrderItem
    from entities.stall_order import StallOrder


class Order(SQLModel, table=True):
    __tablename__: str = "orders"

    f_id: int = Field(default=None, primary_key=True)
    f_total_price: float
    f_created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    f_status: str = Field(default="PENDING")

    order_items: list["OrderItem"] = Relationship(back_populates="order")
    stall_orders: list["StallOrder"] = Relationship(back_populates="order")
