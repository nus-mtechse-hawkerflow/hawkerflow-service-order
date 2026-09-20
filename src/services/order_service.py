from models.order_details import OrderDetails
from models.order_update import OrderUpdate
from repository.order_repo import OrderRepo


class OrderService:
    def __init__(self, repo: OrderRepo):
        self._repo = repo

    def submit_order(self, order: OrderDetails):
        return self._repo.create_order(order)

    def get_order(self, order_id: int):
        return self._repo.get_order(order_id)

    def update_order(self, order_update: OrderUpdate):
        return self._repo.update_order(order_update.order_id, order_update.status)
