import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Override env vars that the Makefile exports from .env before Python starts.
# We force-set clean test values so Settings() validation passes without
# live infrastructure, and dotenv.load_dotenv (override=False) won't undo
# these because they're already set by the time settings.py is imported.
# ---------------------------------------------------------------------------
_TEST_ENV = {
    "TRANSCRIPTION_SERVICES": "[]",
    "STORAGE_SERVICE_NAME": "s3",
    "QUEUE_SERVICE_NAME": "sqs",
    "TRANSCRIPTION_QUEUE_NAME": "test-transcription-queue",
    "TRANSCRIPTION_DEADLETTER_QUEUE_NAME": "test-transcription-queue-deadletter",
    "LLM_QUEUE_NAME": "test-llm-queue",
    "LLM_DEADLETTER_QUEUE_NAME": "test-llm-queue-deadletter",
    "AZURE_SPEECH_KEY": "test-key",
    "AZURE_SPEECH_REGION": "eastus",
    "APP_URL": "http://localhost:3000",
    "REPO": "minute",
    "AUTH_API_URL": "http://localhost:8080",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "test_db",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "postgres",
}
os.environ.update(_TEST_ENV)

# ---------------------------------------------------------------------------
# Prevent SQSQueueService.__init__ from calling GetQueueUrl at import time.
# Route modules instantiate the queue service at module level, so without
# this patch they fail at collection with QueueDoesNotExist.
# ---------------------------------------------------------------------------
_mock_sqs = MagicMock()
_mock_sqs.get_queue_url.return_value = {"QueueUrl": "http://mock-sqs/test-queue"}
_sqs_patch = patch("common.services.queue_services.sqs.get_sqs_client", return_value=_mock_sqs)
_sqs_patch.start()


@pytest.fixture(scope="session", autouse=True)
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
