# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import os
from typing import Any

from dotenv import load_dotenv

# Load environment variables from .env file at runtime
load_dotenv()

# Prevent GoogleAuthError during imports/initialization if GCP credentials are not set up locally
import google.auth
from google.auth import credentials
try:
    google.auth.default()
except Exception:
    google.auth.default = lambda *args, **kwargs: (credentials.AnonymousCredentials(), "mock-project")
    from google.cloud.aiplatform import initializer
    initializer.global_config._project = "mock-project"
    initializer.global_config._location = "us-central1"

if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "False" and "GOOGLE_CLOUD_PROJECT" in os.environ:
    del os.environ["GOOGLE_CLOUD_PROJECT"]

import vertexai
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.cloud import logging as google_cloud_logging
from vertexai.agent_engines.templates.adk import AdkApp
from google.adk.cli.fast_api import get_fast_api_app

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback


class AgentEngineApp(AdkApp):
    def set_up(self) -> None:
        """Initialize the agent engine app with logging and telemetry."""
        vertexai.init()
        setup_telemetry()
        super().set_up()
        logging.basicConfig(level=logging.INFO)
        if os.environ.get("INTEGRATION_TEST") == "TRUE":
            class MockLogger:
                def log_struct(self, data, severity="INFO"):
                    logging.info(f"Mock log_struct ({severity}): {data}")
            self.logger = MockLogger()
        else:
            logging_client = google_cloud_logging.Client()
            self.logger = logging_client.logger(__name__)
        if gemini_location:
            os.environ["GOOGLE_CLOUD_LOCATION"] = gemini_location

    def register_feedback(self, feedback: dict[str, Any]) -> None:
        """Collect and log feedback."""
        feedback_obj = Feedback.model_validate(feedback)
        self.logger.log_struct(feedback_obj.model_dump(), severity="INFO")

    def register_operations(self) -> dict[str, list[str]]:
        """Registers the operations of the Agent."""
        operations = super().register_operations()
        operations[""] = [*operations.get("", []), "register_feedback"]
        return operations

    def clone(self) -> "AgentEngineApp":
        """Returns a clone of the Agent Runtime application."""
        return self

    async def __call__(self, scope, receive, send) -> None:
        """ASGI entry point for local server execution."""
        await fastapi_app(scope, receive, send)


gemini_location = os.environ.get("GOOGLE_CLOUD_LOCATION")
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
agent_runtime = AgentEngineApp(
    app=adk_app,
    artifact_service_builder=lambda: (
        GcsArtifactService(bucket_name=logs_bucket_name)
        if logs_bucket_name
        else InMemoryArtifactService()
    ),
)

# Instantiate the local FastAPI app for local uvicorn execution
fastapi_app = get_fast_api_app(
    agents_dir=os.path.dirname(__file__),
    web=False,
)
