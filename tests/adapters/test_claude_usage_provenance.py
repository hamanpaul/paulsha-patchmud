"""Claude CLI estimate provenance is exercised with an offline fake runner."""

from __future__ import annotations

import json

from patchmud.adapters.claude_cli import ClaudeCliAdapter


def test_character_estimates_are_marked_in_adapter_response():
    adapter = ClaudeCliAdapter(
        runner=lambda _cmd: json.dumps({"result": "ACTION: COMMIT", "usage": {}}),
        clock=iter((1.0, 1.1)).__next__,
    )

    response = adapter.complete([{"role": "user", "content": "x" * 20}])

    assert response.usage_raw["input_tokens"] == 6
    assert response.usage_raw["output_tokens"] == len("ACTION: COMMIT") // 4
    assert response.usage_annotations["input_tokens"]["state"] == "estimated"
    assert response.usage_annotations["input_tokens"]["method"] == "estimate"
    assert response.usage_annotations["input_tokens"]["calculation"] == (
        "prompt_characters_div_4_min_1"
    )
    assert response.usage_annotations["output_tokens"]["state"] == "estimated"


def test_reported_claude_usage_has_no_estimate_annotation():
    adapter = ClaudeCliAdapter(
        runner=lambda _cmd: json.dumps(
            {"result": "ACTION: COMMIT", "usage": {"input_tokens": 5, "output_tokens": 2}}
        ),
        clock=iter((1.0, 1.1)).__next__,
    )

    response = adapter.complete([{"role": "user", "content": "prompt"}])

    assert response.usage_annotations == {}
