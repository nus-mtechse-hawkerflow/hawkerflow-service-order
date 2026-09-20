import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse
import uuid

import boto3

from configurations.app_config import EventsConfig

logger = logging.getLogger("hawkerflow-order.event_publisher")


class EventPublisher:
    """
    Asynchronous event publisher that emits domain events (e.g. OrderReady)
    to AWS SNS or AWS SQS.
    Executes boto3 network calls off the main event loop via asyncio.to_thread.
    """

    def __init__(self, config: EventsConfig, client: Any = None):
        self.config = config
        self._is_sns = bool(config.topic_arn)
        self._client = client or self._init_client()

    def _init_client(self):
        """Initializes the boto3 SNS or SQS client with LocalStack auto-detection."""
        service_name = "sns" if self._is_sns else "sqs"
        client_kwargs: dict[str, Any] = {
            "region_name": self.config.region_name,
        }

        # Auto-detect LocalStack endpoint URL
        endpoint = self.config.endpoint_url
        target_url = self.config.topic_arn if self._is_sns else self.config.notification_queue_url
        if not endpoint and target_url:
            parsed = urlparse(target_url)
            if any(host in parsed.netloc for host in ("localhost", "127.0.0.1", "localstack")):
                endpoint = f"{parsed.scheme}://{parsed.netloc}"

        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
            client_kwargs["verify"] = False
            client_kwargs["aws_access_key_id"] = (
                self.config.access_key_id
                or os.getenv("AWS_ACCESS_KEY_ID")
                or "test"
            )
            client_kwargs["aws_secret_access_key"] = (
                self.config.secret_access_key
                or os.getenv("AWS_SECRET_ACCESS_KEY")
                or "test"
            )
            logger.info("Configured %s client with local endpoint: %s", service_name.upper(), endpoint)
        elif self.config.access_key_id and self.config.secret_access_key:
            client_kwargs["aws_access_key_id"] = self.config.access_key_id
            client_kwargs["aws_secret_access_key"] = self.config.secret_access_key

        return boto3.client(service_name, **client_kwargs)

    async def publish_event(
        self,
        event_type: str,
        data: dict,
        message_attributes: dict | None = None,
    ) -> dict | None:
        """
        Publishes a structured domain event to AWS SNS or SQS.
        Returns the published event payload on success, or None if disabled/failed.
        """
        if not self.config.enabled:
            logger.debug("Event publishing is disabled in configuration.")
            return None

        event_payload = {
            "event_type": event_type,
            "event_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        body_str = json.dumps(event_payload)

        try:
            if self._is_sns:
                sns_attrs = {
                    "event_type": {"DataType": "String", "StringValue": event_type},
                }
                if message_attributes:
                    for k, v in message_attributes.items():
                        sns_attrs[k] = {"DataType": "String", "StringValue": str(v)}

                await asyncio.to_thread(
                    self._client.publish,
                    TopicArn=self.config.topic_arn,
                    Message=body_str,
                    MessageAttributes=sns_attrs,
                )
                logger.info(
                    "📢 Published %s event to SNS topic: %s",
                    event_type,
                    self.config.topic_arn,
                )
            else:
                sqs_target = self.config.notification_queue_url
                if not sqs_target:
                    logger.warning("Cannot publish %s event: notification_queue_url is empty.", event_type)
                    return None

                await asyncio.to_thread(
                    self._client.send_message,
                    QueueUrl=sqs_target,
                    MessageBody=body_str,
                )
                logger.info(
                    "📢 Published %s event to SQS queue: %s",
                    event_type,
                    sqs_target,
                )

            return event_payload

        except Exception as err:
            logger.error("❌ Failed to publish %s event: %s", event_type, err)
            return None

    async def publish_order_ready(self, stall_order: dict) -> dict | None:
        """Publishes an OrderReady event specifically for a stall's sub-order."""
        attrs = {}
        if "stall_id" in stall_order:
            attrs["stall_id"] = str(stall_order["stall_id"])
        if "order_id" in stall_order:
            attrs["order_id"] = str(stall_order["order_id"])
        if "status" in stall_order:
            attrs["status"] = str(stall_order["status"])

        return await self.publish_event(
            event_type="OrderReady",
            data=stall_order,
            message_attributes=attrs,
        )

    async def publish_order_status_updated(self, stall_order: dict) -> dict | None:
        """Publishes an order status update event (e.g. OrderAccepted, OrderPreparing, etc.)"""
        status = str(stall_order.get("status", "UPDATED")).upper()
        event_type = f"Order{status.capitalize()}" if status in (
            "READY", "ACCEPTED", "COMPLETED", "CANCELLED", "PREPARING"
        ) else "OrderStatusUpdated"

        attrs = {}
        if "stall_id" in stall_order:
            attrs["stall_id"] = str(stall_order["stall_id"])
        if "order_id" in stall_order:
            attrs["order_id"] = str(stall_order["order_id"])
        if "status" in stall_order:
            attrs["status"] = str(stall_order["status"])

        return await self.publish_event(
            event_type=event_type,
            data=stall_order,
            message_attributes=attrs,
        )
