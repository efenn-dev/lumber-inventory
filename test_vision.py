"""Tests for the vision module's parsing and fallback behavior.

No live model is involved -- ``_post_chat`` is monkeypatched. What matters is
that the module survives everything a real model actually emits (prose-wrapped
JSON, fences, thinking tags, junk confidences) and that the fallback order and
failure reporting are right.

Run:
    python -m pytest test_vision.py -q
"""

from __future__ import annotations

import json

import pytest

import vision

GOOD = {
    "species_candidates": [
        {"name": "Pecan", "confidence": 0.7, "why": "diamond-furrowed gray bark"},
        {"name": "Black Walnut", "confidence": 0.2, "why": "dark heartwood at the end"},
    ],
    "defects": [{"type": "check", "severity": "minor", "note": "end checking both ends"}],
    "milling_advice": "Open on the check so it lands in a kerf.",
    "caution": "",
}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_bare_json() -> None:
    result = vision._parse_response(json.dumps(GOOD))
    assert result["species_candidates"][0]["name"] == "Pecan"
    assert result["defects"][0]["type"] == "check"


def test_parses_json_wrapped_in_prose_and_fences() -> None:
    """What models actually send, no matter what the prompt says."""
    raw = "Sure! Here is my analysis:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps!"
    assert vision._parse_response(raw)["species_candidates"][0]["name"] == "Pecan"


def test_parses_json_after_thinking_tags_with_stray_braces() -> None:
    """Thinking models put braces in their reasoning; the first { is not the answer."""
    raw = "<think>The bark looks {strange} here, maybe {pecan}?</think>\n" + json.dumps(GOOD)
    result = vision._parse_response(raw)
    assert result["species_candidates"][0]["name"] == "Pecan"


def test_junk_confidence_is_clamped_not_fatal() -> None:
    raw = json.dumps({
        "species_candidates": [{"name": "Pecan", "confidence": "very"},
                               {"name": "Elm", "confidence": 7}],
        "defects": [], "milling_advice": "", "caution": "",
    })
    result = vision._parse_response(raw)
    assert result["species_candidates"][0]["confidence"] == 0.0
    assert result["species_candidates"][1]["confidence"] == 1.0


def test_candidates_are_capped_and_malformed_entries_dropped() -> None:
    raw = json.dumps({
        "species_candidates": [{"name": f"S{i}", "confidence": 0.5} for i in range(6)]
        + [{"confidence": 0.9}, "not a dict"],
        "defects": [{"note": "no type"}],
        "milling_advice": "", "caution": "",
    })
    result = vision._parse_response(raw)
    assert len(result["species_candidates"]) == 3
    assert result["defects"] == []


def test_no_json_at_all_is_an_error() -> None:
    with pytest.raises(ValueError):
        vision._parse_response("I cannot see a log in this image.")


def test_unbalanced_json_is_an_error() -> None:
    with pytest.raises(ValueError):
        vision._parse_response('{"species_candidates": [')


# --------------------------------------------------------------------------
# Backend order and failure reporting
# --------------------------------------------------------------------------


def test_local_backend_is_tried_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, key: str, model: str, prompt: str, image: str) -> str:
        calls.append(url)
        return json.dumps(GOOD)

    monkeypatch.setattr(vision, "_post_chat", fake_post)
    result = vision.analyze_log_photo(b"jpeg", ["Pecan"])
    assert calls == [vision.LOCAL_URL]
    assert result["_backend"].startswith("local")


def test_fallback_is_used_when_local_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision, "FALLBACK_URL", "https://api.example.com/v1")
    monkeypatch.setattr(vision, "FALLBACK_KEY", "sk-test")

    def fake_post(url: str, key: str, model: str, prompt: str, image: str) -> str:
        if url == vision.LOCAL_URL:
            raise ConnectionRefusedError("connection refused")
        assert key == "sk-test"
        return json.dumps(GOOD)

    monkeypatch.setattr(vision, "_post_chat", fake_post)
    result = vision.analyze_log_photo(b"jpeg", ["Pecan"])
    assert result["_backend"].startswith("fallback")


def test_total_failure_names_every_backend_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision, "FALLBACK_URL", "https://api.example.com/v1")

    def fake_post(url: str, key: str, model: str, prompt: str, image: str) -> str:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(vision, "_post_chat", fake_post)
    with pytest.raises(vision.VisionUnavailable) as excinfo:
        vision.analyze_log_photo(b"jpeg", ["Pecan"])
    message = str(excinfo.value)
    assert "local" in message and "fallback" in message
    assert "start-visual-debug-llama" in message


def test_species_hints_reach_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_post(url: str, key: str, model: str, prompt: str, image: str) -> str:
        seen.append(prompt)
        return json.dumps(GOOD)

    monkeypatch.setattr(vision, "_post_chat", fake_post)
    vision.analyze_log_photo(b"jpeg", ["Pecan", "Eastern Redcedar"])
    assert "Pecan, Eastern Redcedar" in seen[0]
