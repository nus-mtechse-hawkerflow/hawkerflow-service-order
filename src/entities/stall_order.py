from sqlmodel import SQLModel, Field, Relationship


class StallOrder(SQLModel, table=True):
    __tablename__ = "stall_orders"

    f_id: int = Field(default=None, primary_key=True)
    f_order_id: int = Field(foreign_key="orders.f_id")
    f_stall_id: int = Field(index=True)
    f_status: str = Field(default="PENDING")
    f_subtotal: float

    order: "Order" = Relationship(back_populates="stall_orders")
    items: list["OrderItem"] = Relationship(back_populates="stall_order")
