from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class OrderRequest(SQLModel, table=True):
    """
    Links the order_ref handed to a diner when an order is queued to the order
    the SQS worker created from it. A table of its own, rather than a column on
    orders, so create_all adds it to existing databases without a migration.
    """
    __tablename__ = "order_requests"

    f_order_ref: str = Field(primary_key=True)
    f_order_id: int = Field(foreign_key="orders.f_id")
    f_created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
