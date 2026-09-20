from pydantic import BaseModel


class OrderUpdate(BaseModel):
    status: str
    order_id: int
