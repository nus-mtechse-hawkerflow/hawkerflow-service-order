from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from models.order_details import OrderDetails
from models.order_update import OrderUpdate
from services.order_service import OrderService


order_router = APIRouter(prefix='/v1/order')


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


@order_router.post('/orders')
async def submit_order(
        orders: OrderDetails,
        order_service: Annotated[OrderService, None]=Depends(get_order_service)
):
    order_placed = order_service.submit_order(orders)
    return JSONResponse(
        content={
            "message": "Order submitted",
            **order_placed
        }
    )


@order_router.get("/orders/{order_id}")
async def get_order(
        order_id: int,
        order_service: Annotated[OrderService, None]=Depends(get_order_service)
):
    order_details = order_service.get_order(order_id)

    return JSONResponse(
        content={
            "message": "Order retrieved",
            **order_details
        }
    )


@order_router.put("/orders/update")
async def get_order(
        order_update: OrderUpdate,
        order_service: Annotated[OrderService, None]=Depends(get_order_service)
):
    order_details = order_service.update_order(order_update)

    return JSONResponse(
        content={
            "message": "Order Updated",
            "status": order_details.f_status
        }
    )
