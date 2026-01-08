from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from innovation_intelligence.config import settings
from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class BedrockClient:
    """
    Thin wrapper around the Bedrock Runtime client, with basic retry logic.
    """

    model_id: Optional[str] = None
    region: Optional[str] = None
    profile: Optional[str] = None

    def __post_init__(self) -> None:
        self.region = self.region or settings.aws.region
        self.profile = self.profile or settings.aws.profile
        self.model_id = self.model_id or settings.aws.bedrock_model_id

        if self.profile:
            session = boto3.Session(profile_name=self.profile)
        else:
            session = boto3.Session()

        self._client = session.client("bedrock-runtime", region_name=self.region)

    # ------------------------------------------------------------------ #
    # Low-level invoke
    # ------------------------------------------------------------------ #
    def invoke(
        self,
        body: Dict[str, Any],
        *,
        model_id: Optional[str] = None,
        retry: int = 3,
        backoff: float = 2.0,
    ) -> Dict[str, Any]:
        """
        Invoke the Bedrock model with a JSON body and return the parsed JSON response.
        """
        mid = model_id or self.model_id

        for attempt in range(1, retry + 1):
            try:
                resp = self._client.invoke_model(
                    modelId=mid,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
                response_body = json.loads(resp["body"].read())
                return response_body

            except (self._client.exceptions.ThrottlingException,) as e:
                if attempt == retry:
                    log.error(f"[Bedrock] Throttled after {retry} attempts: {e}")
                    raise
                wait = backoff * (2 ** (attempt - 1))
                log.warning(f"[Bedrock] Throttled, retrying in {wait:.1f}s...")
                time.sleep(wait)

            except (self._client.exceptions.ModelTimeoutException,) as e:
                if attempt == retry:
                    log.error(f"[Bedrock] Timeout after {retry} attempts: {e}")
                    raise
                wait = backoff * attempt
                log.warning(f"[Bedrock] Timeout, retrying in {wait:.1f}s...")
                time.sleep(wait)

            except (BotoCoreError, ClientError, Exception) as e:
                if attempt == retry:
                    log.error(f"[Bedrock] Error after {retry} attempts: {e}")
                    raise
                wait = backoff * attempt
                log.warning(f"[Bedrock] Error {e}, retrying in {wait:.1f}s...")
                time.sleep(wait)

        # Should never reach here
        raise RuntimeError("Bedrock invoke failed unexpectedly")
