"""
Gemini client wrapper for Vertex AI.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError

from innovation_intelligence.logger import get_logger

log = get_logger(__name__)


@dataclass
class GeminiClient:
    """
    Thin wrapper around the Gemini client for Vertex AI,
    with basic retry logic and tool-use support.
    """

    project: str
    location: str = "global"
    model_id: str = "gemini-2.5-flash"
    _client: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )

    def invoke(
        self,
        contents: List[Any],
        *,
        tool_defs: Optional[List[types.Tool]] = None,
        system_instruction: Optional[str] = None,
        allowed_function_names: Optional[List[str]] = None,
        model_id: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
        retry: int = 3,
        backoff: float = 2.0,
    ) -> types.GenerateContentResponse:
        """
        Invoke Gemini model with optional tool use configuration.
        """
        mid = model_id or self.model_id

        # Build config
        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if tool_defs:
            config_kwargs["tools"] = tool_defs

            # FORCE tool use if function names specified (Mode = ANY)
            if allowed_function_names:
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY",
                        allowed_function_names=allowed_function_names,
                    )
                )

        config = types.GenerateContentConfig(**config_kwargs)

        # Invoke with retry logic
        for attempt in range(1, retry + 1):
            try:
                response = self._client.models.generate_content(
                    model=mid,
                    contents=contents,
                    config=config,
                )
                return response

            except APIError as e:
                if attempt == retry:
                    log.error(f"[Gemini] All {retry} attempts failed: {e}")
                    raise

                error_msg = str(e)

                if "RESOURCE_EXHAUSTED" in error_msg or "429" in error_msg:
                    wait_time = backoff * (2 ** (attempt - 1))
                    log.warning(f"[Gemini] Rate limited, waiting {wait_time}s")
                else:
                    wait_time = backoff * attempt
                    log.warning(
                        f"[Gemini] Attempt {attempt} failed: {e}, retrying in {wait_time}s"
                    )

                time.sleep(wait_time)

        raise RuntimeError("Gemini invoke failed unexpectedly")