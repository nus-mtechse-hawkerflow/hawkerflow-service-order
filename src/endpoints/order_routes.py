from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from dependencies.auth import get_current_stall_id, verify_stall_access
from models.order_details import OrderDetails
from models.order_update import OrderUpdate
from models.stall_order_update import StallOrderUpdate
from services.order_service import OrderService

order_router = APIRouter(prefix="/v1/order")


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


# ---------------- Diner & General Endpoints ----------------
@order_router.post("/orders")
async def submit_order(
    orders: OrderDetails,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    order_placed = order_service.submit_order(orders)
    return JSONResponse(
        content={
            "message": "Order submitted",
            **order_placed,
        }
    )


@order_router.get("/orders/{order_id}")
async def get_order(
    order_id: int,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    order_details = order_service.get_order(order_id)
    if not order_details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found",
        )

    return JSONResponse(
        content={
            "message": "Order retrieved",
            **order_details,
        }
    )


@order_router.put("/orders/update")
async def update_order(
    order_update: OrderUpdate,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    order_details = order_service.update_order(order_update)
    if not order_details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_update.order_id} not found",
        )

    return JSONResponse(
        content={
            "message": "Order Updated",
            "status": order_details.f_status,
        }
    )


# ---------------- Hawker / Stall Tenant-Isolated Endpoints ----------------

@order_router.get("/stalls/me/orders")
async def get_my_stall_orders(
    current_stall_id: Annotated[int, Depends(get_current_stall_id)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    order_status: Annotated[str | None, Query(alias="status")] = None,
):
    """
    Fetch all orders belonging to the currently authenticated hawker's stall.
    Hawkers only ever receive dishes and orders intended for their stall.
    """
    stall_orders = order_service.get_orders_for_stall(current_stall_id, order_status)
    return JSONResponse(
        content={
            "stall_id": current_stall_id,
            "orders": stall_orders,
        }
    )


@order_router.get("/stalls/{stall_id}/orders")
async def get_stall_orders(
    stall_id: int,
    current_stall_id: Annotated[int, Depends(get_current_stall_id)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
    order_status: Annotated[str | None, Query(alias="status")] = None,
):
    """
    Fetch all orders for a specific stall ID.
    Enforces that caller is authenticated and authorized for that stall_id.
    """
    verify_stall_access(requested_stall_id=stall_id, authenticated_stall_id=current_stall_id)
    stall_orders = order_service.get_orders_for_stall(stall_id, order_status)
    return JSONResponse(
        content={
            "stall_id": stall_id,
            "orders": stall_orders,
        }
    )


@order_router.patch("/stalls/{stall_id}/orders/{order_id}")
async def update_stall_order_status(
    stall_id: int,
    order_id: int,
    body: StallOrderUpdate,
    current_stall_id: Annotated[int, Depends(get_current_stall_id)],
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    """
    Allows a hawker to update preparation status (e.g. PREPARING, READY, COMPLETED)
    for items belonging ONLY to their stall in the specified order.
    """
    verify_stall_access(requested_stall_id=stall_id, authenticated_stall_id=current_stall_id)
    result = await order_service.update_stall_order_status(stall_id, order_id, body.status)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No order found for stall {stall_id} under order {order_id}",
        )

    return JSONResponse(
        content={
            "message": "Stall order status updated",
            **result,
        }
    )


# ---------------- SQS Diagnostics & Simulation ----------------

@order_router.get("/sqs/status")
async def get_sqs_worker_status(request: Request):
    """
    Check the current health and operational status of the SQS background poller.
    """
    worker = getattr(request.app.state, "sqs_worker", None)
    config = getattr(request.app.state, "config", None)

    if not worker:
        return JSONResponse(
            content={
                "enabled": config.sqs.enabled if config and config.sqs else False,
                "is_running": False,
                "message": "SQS Worker is not running (disabled in configuration or initialization failed).",
                "config": config.sqs.model_dump() if config and config.sqs else None,
            }
        )

    return JSONResponse(content=worker.get_status())


@order_router.post("/sqs/simulate")
async def simulate_sqs_message(
    payload: dict,
    order_service: Annotated[OrderService, Depends(get_order_service)],
):
    """
    Simulate an incoming SQS order event without requiring an active AWS connection.
    Useful for local testing, verification, and automated pipelines.
    """
    result = order_service.process_incoming_sqs_message(payload)
    return JSONResponse(
        content={
            "message": "Simulated SQS message processed successfully",
            "result": result,
        }
    )

