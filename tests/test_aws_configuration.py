import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from configurations.app_config import AppConfig, SqsConfig
from lifecycle.lifespan import create_queue_worker
from services.event_publisher import EventPublisher

REPO_ROOT = str(Path(__file__).resolve().parents[1])
QUEUE_URL = "https://sqs.ap-southeast-1.amazonaws.com/123456789012/hawkerflow-dev-order-queue"
TOPIC_ARN = "arn:aws:sns:ap-southeast-1:123456789012:hawkerflow-dev-order-status"


class TestConfigurationFromEnvironment(unittest.TestCase):
    """
    On AWS the task definition supplies the queue and event settings as
    environment variables, which must win over the LocalStack values that
    resources/config.yml ships with.
    """

    def _config(self, env: dict[str, str]) -> AppConfig:
        with patch.dict(os.environ, {"PROJECT_ROOT": REPO_ROOT, **env}):
            return AppConfig()

    def test_config_yml_alone_leaves_the_queue_off(self):
        config = self._config({})

        self.assertIn("localhost", config.sqs.queue_url)
        self.assertFalse(config.sqs.enabled)

    def test_environment_overrides_the_queue_settings(self):
        config = self._config({
            "SQS__ENABLED": "true",
            "SQS__QUEUE_URL": QUEUE_URL,
            "SQS__REGION_NAME": "ap-southeast-1",
        })

        self.assertTrue(config.sqs.enabled)
        self.assertEqual(config.sqs.queue_url, QUEUE_URL)
        # No endpoint override: boto3 must talk to real SQS with the task role
        self.assertIsNone(config.sqs.endpoint_url)
        # Settings the environment does not mention still come from config.yml
        self.assertEqual(config.sqs.visibility_timeout, 60)

    def test_config_yml_alone_leaves_event_publishing_off(self):
        config = self._config({})

        self.assertFalse(config.events.enabled)

    def test_environment_turns_event_publishing_on_for_a_real_topic(self):
        config = self._config({
            "EVENTS__ENABLED": "true",
            "EVENTS__TOPIC_ARN": TOPIC_ARN,
            "EVENTS__REGION_NAME": "ap-southeast-1",
        })

        self.assertTrue(config.events.enabled)
        self.assertEqual(config.events.topic_arn, TOPIC_ARN)
        # No endpoint override: boto3 must talk to real SNS with the task role
        self.assertIsNone(config.events.endpoint_url)

    def test_publisher_for_a_real_topic_uses_the_task_role_and_fails_fast(self):
        config = self._config({"EVENTS__ENABLED": "true", "EVENTS__TOPIC_ARN": TOPIC_ARN})

        with patch("services.event_publisher.boto3.client", return_value=MagicMock()) as client:
            EventPublisher(config.events)

        service_name = client.call_args.args[0]
        kwargs = client.call_args.kwargs
        self.assertEqual(service_name, "sns")
        self.assertNotIn("endpoint_url", kwargs)
        self.assertNotIn("aws_access_key_id", kwargs)
        # Publishing is awaited while the diner waits, so it must not hang
        self.assertLessEqual(kwargs["config"].connect_timeout, 5)
        self.assertLessEqual(kwargs["config"].read_timeout, 5)

    def test_database_settings_are_unaffected(self):
        config = self._config({"SQS__QUEUE_URL": QUEUE_URL})

        self.assertEqual(config.datasource.database.driver_name, "postgresql+psycopg2")


class TestQueueComponents(unittest.TestCase):
    def test_enabled_queue_creates_a_worker(self):
        sqs = SqsConfig(enabled=True, queue_url=QUEUE_URL, region_name="ap-southeast-1")

        with patch("workers.sqs_worker.boto3.client", return_value=MagicMock()) as client:
            worker = create_queue_worker(sqs, MagicMock())

        self.assertIsNotNone(worker)
        # A real AWS queue must use the task role, not LocalStack's dummy keys
        for call in client.call_args_list:
            self.assertNotIn("endpoint_url", call.kwargs)
            self.assertNotIn("aws_access_key_id", call.kwargs)

    def test_disabled_queue_creates_nothing(self):
        sqs = SqsConfig(enabled=False, queue_url=QUEUE_URL)

        self.assertIsNone(create_queue_worker(sqs, MagicMock()))

    def test_missing_queue_configuration_creates_nothing(self):
        self.assertIsNone(create_queue_worker(None, MagicMock()))
        no_url = SqsConfig(enabled=True, queue_url="")
        self.assertIsNone(create_queue_worker(no_url, MagicMock()))


if __name__ == "__main__":
    unittest.main()
