from pydantic import BaseModel


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
