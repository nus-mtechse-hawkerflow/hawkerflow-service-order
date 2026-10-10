from sqlmodel import Field, SQLModel


class OrderOption(SQLModel, table=True):
    """
    How the diner takes an order away. A table of its own, rather than columns
    on orders, so create_all adds it to existing databases without a migration.
    Orders without a row predate it and are dine-in.
    """
    __tablename__ = "order_options"

    f_order_id: int = Field(foreign_key="orders.f_id", primary_key=True)
    f_dining_option: str = Field(default="dine_in")
    f_takeaway_fee: float = Field(default=0.0)
