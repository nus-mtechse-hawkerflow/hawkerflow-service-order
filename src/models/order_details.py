from typing import Literal

from pydantic import BaseModel, Field


class Dish(BaseModel):
    dish_id: int
    dish_name: str
    quantity: int
    price: float


class Order(BaseModel):
    stall_id: int
    dishes: list[Dish]


class OrderDetails(BaseModel):
    orders: list[Order]
    total_price: float
    # Both are self-collect; takeaway tells the stall to pack the order.
    # Optional so callers that predate them (the hawker POS) keep working.
    dining_option: Literal["dine_in", "takeaway"] = "dine_in"
    takeaway_fee: float = Field(default=0.0, ge=0)
