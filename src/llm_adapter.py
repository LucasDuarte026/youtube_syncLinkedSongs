"""Structured Output LLM adapter using direct Gemini 3.8 Medium via AgyClient."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Type, TypeVar

from pydantic import BaseModel

# Ensure Maestro root is in sys.path so `core.llm` can be imported directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAESTRO_ROOT = PROJECT_ROOT.parent.parent
if str(MAESTRO_ROOT) not in sys.path:
    sys.path.insert(0, str(MAESTRO_ROOT))

from core.llm import AgyClient, DISCORD_CONTINGENCY_NOTICE  # noqa: E402

logger = logging.getLogger("ytmusic_taxonomist.llm")

T = TypeVar("T", bound=BaseModel)


def extract_json_payload(raw_text: str) -> Dict[str, Any]:
    """Strip <think> blocks, contingency notices, and markdown fences, returning a parsed JSON dict."""
    if not raw_text:
        raise ValueError("Empty LLM response text.")

    cleaned = raw_text
    if DISCORD_CONTINGENCY_NOTICE in cleaned:
        cleaned = cleaned.replace(DISCORD_CONTINGENCY_NOTICE, "")

    # Strip <think>...</think> blocks emitted by reasoning models
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL | re.IGNORECASE).strip()

    # Extract content inside ```json ... ``` or ``` ... ``` if present
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        return json.loads(candidate)

    # Locate outermost JSON object braces
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = cleaned[start_idx : end_idx + 1]
        return json.loads(candidate)

    return json.loads(cleaned)


class StructuredLLMClient:
    """Adapter that delegates directly to local Gemini 3.8 Flash Medium via AgyClient, bypassing external stacks."""

    def __init__(
        self,
        model: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.5,
        custom_runner: Optional[Callable[[str, str], str]] = None,
        agy_client: Optional[AgyClient] = None,
    ) -> None:
        self.model = model or os.environ.get("YTMUSIC_LLM_MODEL", "gemini-3.8-flash-medium")
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.custom_runner = custom_runner
        self.agy_client = agy_client or AgyClient()

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        temperature: float = 0.15,
        max_tokens: int = 4096,
    ) -> T:
        """Invoke Gemini 3.8 Flash Medium via AgyClient and validate output against `response_model` with retry."""
        schema_json = json.dumps(response_model.model_json_schema(), ensure_ascii=False, indent=2)
        sys_instructions = (
            system_prompt
            or "Você é um Arquiteto Musical e Taxonomista Especialista do Maestro AI."
        )
        full_system_prompt = (
            f"{sys_instructions}\n\n"
            "IMPORTANTE: Responda EXCLUSIVAMENTE com um objeto JSON válido (sem texto extra antes ou depois) "
            "seguindo estritamente este JSON Schema:\n"
            f"{schema_json}"
        )

        combined_prompt = f"""{full_system_prompt}

### DADOS PARA PROCESSAMENTO:
{prompt}
"""

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.custom_runner is not None:
                    raw_content = self.custom_runner(prompt, full_system_prompt)
                else:
                    res = self.agy_client.query(
                        prompt=combined_prompt,
                        model=self.model,
                        timeout=120,
                        dangerously_skip_permissions=True,
                    )
                    if res.exit_code != 0:
                        raise RuntimeError(f"AGY CLI returned code {res.exit_code}: {res.raw_stderr}")
                    raw_content = res.content

                parsed_dict = extract_json_payload(raw_content)
                return response_model.model_validate(parsed_dict)
            except Exception as err:
                last_error = err
                logger.warning(
                    "[StructuredLLMClient] Tentativa %d/%d falhou (%s). Retrying...",
                    attempt,
                    self.max_retries,
                    err,
                )
                if attempt < self.max_retries:
                    sleep_time = self.base_delay * (2 ** (attempt - 1))
                    time.sleep(sleep_time)

        raise RuntimeError(
            f"Falha ao obter resposta estruturada ({response_model.__name__}) após {self.max_retries} tentativas: {last_error}"
        ) from last_error
