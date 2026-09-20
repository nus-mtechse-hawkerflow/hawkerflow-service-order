import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from configurations.app_config import SqsConfig
from services.order_service import OrderService

logger = logging.getLogger("hawkerflow-order.sqs_worker")


class SqsWorker:
    """
    Background worker that continuously long-polls an AWS SQS queue for order events.
    Executes synchronous boto3 network calls off the main event loop via asyncio.to_thread.
    """

    def __init__(
        self,
        config: SqsConfig,
        order_service: OrderService,
        sqs_client: Any = None,
    ):
        self.config = config
        self.order_service = order_service
        self._stop_event = asyncio.Event()

        # Diagnostics & Health Monitoring
        self.is_running: bool = False
        self.messages_processed: int = 0
        self.last_poll_at: str | None = None
        self.last_error: str | None = None

        if sqs_client is not None:
            self._sqs = sqs_client
        else:
            self._sqs = self._init_sqs_client()

    def _init_sqs_client(self):
        """Initializes the boto3 SQS client, auto-detecting LocalStack and dev credentials."""
        client_kwargs: dict[str, Any] = {
            "region_name": self.config.region_name,
        }

        # Determine endpoint URL
        endpoint = self.config.endpoint_url
        if not endpoint and self.config.queue_url:
            parsed = urlparse(self.config.queue_url)
            if any(host in parsed.netloc for host in ("localhost", "127.0.0.1", "localstack")):
                endpoint = f"{parsed.scheme}://{parsed.netloc}"

        if endpoint:
            client_kwargs["endpoint_url"] = endpoint
            # Local development / LocalStack SSL handling
            client_kwargs["verify"] = False
            # Default dummy credentials for LocalStack if none are provided
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
            logger.info("Configured SQS client for local endpoint: %s", endpoint)
        elif self.config.access_key_id and self.config.secret_access_key:
            client_kwargs["aws_access_key_id"] = self.config.access_key_id
            client_kwargs["aws_secret_access_key"] = self.config.secret_access_key

        return boto3.client("sqs", **client_kwargs)

    async def start(self) -> None:
        """Continuously long-polls SQS until stop() is called or task is cancelled."""
        logger.info(
            "🚀 SQS Worker started polling queue: %s (region: %s, wait_time: %ds)",
            self.config.queue_url,
            self.config.region_name,
            self.config.wait_time_seconds,
        )
        self._stop_event.clear()
        self.is_running = True

        while not self._stop_event.is_set():
            try:
                self.last_poll_at = datetime.now(timezone.utc).isoformat()

                # Long-poll SQS in a thread pool to avoid blocking FastAPI's async loop
                response = await asyncio.to_thread(
                    self._sqs.receive_message,
                    QueueUrl=self.config.queue_url,
                    MaxNumberOfMessages=self.config.max_number_of_messages,
                    WaitTimeSeconds=self.config.wait_time_seconds,
                    VisibilityTimeout=self.config.visibility_timeout,
                    AttributeNames=["All"],
                    MessageAttributeNames=["All"],
                )

                self.last_error = None
                messages = response.get("Messages", [])
                if messages:
                    logger.info("📥 Received %d order message(s) from SQS", len(messages))
                    await asyncio.gather(
                        *(self._handle_message(msg) for msg in messages),
                        return_exceptions=True,
                    )

            except asyncio.CancelledError:
                logger.info("SQS worker polling loop received cancellation.")
                break
            except NoCredentialsError:
                err_msg = (
                    "No AWS credentials found. Please set AWS_ACCESS_KEY_ID and "
                    "AWS_SECRET_ACCESS_KEY in environment or resources/config.yml"
                )
                self.last_error = err_msg
                logger.error("⚠️ SQS Auth Error: %s. Retrying in 5s...", err_msg)
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
            except EndpointConnectionError as e:
                err_msg = f"Could not connect to SQS endpoint at {self.config.queue_url}: {e}"
                self.last_error = err_msg
                logger.warning("⚠️ SQS Connection Error: %s. Is LocalStack/AWS running? Retrying in 5s...", err_msg)
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
            except ClientError as e:
                err_msg = f"AWS SQS ClientError: {e}"
                self.last_error = err_msg
                logger.error("⚠️ %s. Backing off for 5s...", err_msg)
                try:
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    break
            except Exception as e:
                err_msg = f"Unexpected error in SQS polling loop: {e}"
                self.last_error = err_msg
                logger.exception("⚠️ %s. Retrying in 3s...", err_msg)
                try:
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break

        self.is_running = False
        logger.info("🛑 SQS Worker poller stopped gracefully.")

    async def _handle_message(self, message: dict) -> None:
        receipt_handle = message.get("ReceiptHandle")
        message_id = message.get("MessageId")
        body = message.get("Body", "")

        logger.info("Processing SQS message ID: %s", message_id)
        try:
            payload = json.loads(body)

            import inspect
            # Delegate to business logic in OrderService (coroutine or sync)
            if inspect.iscoroutinefunction(self.order_service.process_incoming_sqs_message):
                await self.order_service.process_incoming_sqs_message(payload)
            else:
                await asyncio.to_thread(
                    self.order_service.process_incoming_sqs_message,
                    payload,
                )

            # Delete the message on successful completion
            await asyncio.to_thread(
                self._sqs.delete_message,
                QueueUrl=self.config.queue_url,
                ReceiptHandle=receipt_handle,
            )
            self.messages_processed += 1
            logger.info("✅ Successfully processed and deleted SQS message: %s", message_id)

        except json.JSONDecodeError as err:
            logger.error("❌ Failed to parse JSON body for message %s: %s", message_id, err)
        except Exception as err:
            logger.error(
                "❌ Error processing message %s: %s. Leaving message in queue for retry/DLQ.",
                message_id,
                err,
            )

    def stop(self) -> None:
        """Signals the background worker to stop polling."""
        logger.info("Signaling SQS Worker to stop...")
        self._stop_event.set()
        self.is_running = False

    def get_status(self) -> dict:
        """Returns the current operational status of the SQS worker."""
        return {
            "enabled": self.config.enabled,
            "is_running": self.is_running,
            "queue_url": self.config.queue_url,
            "region_name": self.config.region_name,
            "wait_time_seconds": self.config.wait_time_seconds,
            "messages_processed": self.messages_processed,
            "last_poll_at": self.last_poll_at,
            "last_error": self.last_error,
        }
