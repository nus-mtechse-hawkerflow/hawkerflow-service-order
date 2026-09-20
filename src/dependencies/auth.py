import base64
import json
import logging
from typing import Annotated

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)


def _decode_jwt_payload(token: str) -> dict:
    """Safely extracts JWT payload claims without external library dependency."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        # Pad base64 string if necessary
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        return json.loads(decoded)
    except Exception as e:
        logger.warning("Failed to decode JWT payload: %s", e)
        return {}


async def get_current_stall_id(
    x_stall_id: Annotated[str | None, Header(alias="X-Stall-ID")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> int:
    """
    Extracts and authenticates the stall ID from the request headers.
    Supports:
      1. Direct `X-Stall-ID` header (microservice-to-microservice or local dev).
      2. `Authorization: Bearer <jwt>` with claims `custom:stall_id` or `stall_id`.
    """
    # 1. Direct header
    if x_stall_id:
        try:
            return int(x_stall_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Stall-ID header: must be an integer",
            )

    # 2. Bearer token / JWT
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims = _decode_jwt_payload(token)
        raw_stall = claims.get("custom:stall_id") or claims.get("stall_id")
        if raw_stall:
            try:
                return int(raw_stall)
            except ValueError:
                pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: missing valid stall credentials (X-Stall-ID or Bearer token)",
    )


def verify_stall_access(requested_stall_id: int, authenticated_stall_id: int) -> None:
    """
    Ensures that the authenticated hawker only accesses orders for their assigned stall.
    Raises 403 Forbidden if a hawker attempts cross-tenant access.
    """
    if requested_stall_id != authenticated_stall_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: You are not authorized to view or manage orders for stall {requested_stall_id}",
        )
