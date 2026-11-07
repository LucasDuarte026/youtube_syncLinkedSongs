"""Unit tests for src/llm_adapter.py (Task 2)."""

import pytest
from core.llm import AgyResponse
from src.models import TaxonomyCatalogResponse


def test_extract_json_payload_handles_thinking_tags_and_markdown_fences():
    from src.llm_adapter import extract_json_payload

    raw = """<think>
Let me analyze the genres first...
</think>
Here is the structured output:
```json
{
  "categories": [
    {"name": "Funk - Mandelão", "description": "Batida pesada automotiva"}
  ]
}
```
*(⚠️ Modelos externos indisponíveis. Respondido via contingência local: GPT-OSS 120B)*"""

    data = extract_json_payload(raw)
    assert "categories" in data
    assert data["categories"][0]["name"] == "Funk - Mandelão"


def test_structured_llm_client_delegates_to_gemini_and_retries(monkeypatch):
    import src.llm_adapter as adapter_mod

    calls = {"count": 0}

    class FakeAgyClient:
        def query(self, prompt, model="gemini-3.8-flash-medium", **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return AgyResponse(content="not valid json at all", model=model, exit_code=0)
            return AgyResponse(
                content='{"categories": [{"name": "Rock - Indie & Alternativo", "description": "Guitarras indie"}]}',
                model=model,
                exit_code=0,
            )

    client = adapter_mod.StructuredLLMClient(
        max_retries=3,
        base_delay=0.01,
        agy_client=FakeAgyClient(),
    )
    result = client.generate_structured(
        prompt="Test taxonomy prompt",
        response_model=TaxonomyCatalogResponse,
    )
    assert isinstance(result, TaxonomyCatalogResponse)
    assert len(result.categories) == 1
    assert result.categories[0].name == "Rock - Indie & Alternativo"
    assert calls["count"] == 2
