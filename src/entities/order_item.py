from sqlmodel import Field, Relationship, SQLModel


class OrderItem(SQLModel, table=True):

    __tablename__ = "order_items"

    f_id: int = Field(default=None, primary_key=True)
    f_order_id: int = Field(foreign_key="orders.f_id")
    f_stall_id: int
    f_dish_id: int
    f_quantity: int
    f_price: float

    order: "Order" = Relationship(back_populates="order_items")
