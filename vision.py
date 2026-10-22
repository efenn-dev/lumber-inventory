"""AI photo analysis for logs: species candidates, defects, milling advice.

Talks to any OpenAI-compatible chat-completions endpoint that accepts images.
Two backends, tried in order:

1. **Local** (``LUMBER_VISION_URL``, default the Qwen3-VL llama-server on
   :18080 -- start it with
   ``app-manager-mcp/scripts/start-visual-debug-llama.ps1``). Free, private,
   and good enough to sort pecan from cedar from oak.
2. **Fallback** (``LUMBER_VISION_FALLBACK_URL`` + ``_KEY`` + ``_MODEL``) --
   any hosted OpenAI-compatible API, for when the local server is down or a
   second opinion is wanted.

Design rules learned from the gaming pipeline's VLM work, which hit every one
of these failure modes the hard way:

* **Long timeout.** A local VL model cold-loads for 2-3 minutes; a short
  client timeout turns a working backend into a "broken" one.
* **The model proposes, the user disposes.** Species ID from a photo is
  genuinely hard -- a field-scale VL model is reliable at genus level (oak vs
  pecan vs cedar) and shaky at species level. Everything returned here is a
  suggestion the UI asks the user to confirm, never a value written silently.
* **Constrain the candidate list.** Asking "what species is this?" invites
  hallucinated exotics. Asking "which of these nine North Texas species is
  this, or 'other'?" gets a small model to a useful answer.
* **Never trust the model to return bare JSON.** Extract the first balanced
  object from whatever prose it wrapped around it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

#: Cold-load reality: first call after the model loads can take minutes.
TIMEOUT_S = 240

LOCAL_URL = os.environ.get("LUMBER_VISION_URL", "http://127.0.0.1:18080/v1").rstrip("/")
LOCAL_MODEL = os.environ.get("LUMBER_VISION_MODEL", "local")
FALLBACK_URL = os.environ.get("LUMBER_VISION_FALLBACK_URL", "").rstrip("/")
FALLBACK_KEY = os.environ.get("LUMBER_VISION_FALLBACK_KEY", "")
FALLBACK_MODEL = os.environ.get("LUMBER_VISION_FALLBACK_MODEL", "gpt-4o-mini")


class VisionUnavailable(RuntimeError):
    """Raised when every configured backend failed; message says which and why."""


PROMPT_TEMPLATE = """You are helping a small sawmill operator in North Texas assess a log from a photo.

Species on this property are almost always one of: {species_list} -- or occasionally something else.

Use this identification key. It OVERRIDES your general impressions -- tested
against this exact model, whose instinct is to call everything Black Walnut:

- End grain with light rays clearly radiating from the pith: OAK (Red Oak,
  White Oak, or Post Oak). No other species here shows prominent rays; walnut
  and pecan rays are nearly invisible. Visible rays RULE OUT walnut.
- End grain chocolate to purple-brown heartwood: Black Walnut -- the only
  species here that dark. Tan or medium brown is NOT walnut.
- End grain tan to pinkish brown, darker streaks, large earlywood pores:
  Pecan.
- Purple-red heart with a bright white sapwood band, very fine grain, small
  log: Eastern Redcedar. Extremely dense dark amber, small twisted log:
  Mesquite. Wide pale sapwood, gray-blue spalt lines: Hackberry. Wavy
  interlocked banding in the latewood: Elm.
- Bark and leaves alone are WEAK evidence. Pecan and walnut both have pinnate
  compound leaves and furrowed bark and are routinely confused -- from bark or
  foliage alone, list both, most likely first, and cap every confidence at
  0.5. Say in "why" that end grain is needed to be sure.

Confidence above 0.8 requires clear end-grain evidence matching the key.

Look at the bark, end grain, and overall form. Respond with ONLY a JSON object, no other text:

{{
  "species_candidates": [
    {{"name": "<species>", "confidence": <0.0-1.0>, "why": "<one sentence: the visible evidence>"}}
  ],
  "defects": [
    {{"type": "<check|shake|rot|insect|spalting|metal_stain|sweep|crook|knots|other>",
      "severity": "<minor|moderate|severe>", "note": "<one sentence>"}}
  ],
  "milling_advice": "<one or two sentences: how the visible defects should steer the cut>",
  "caution": "<anything that makes this log risky to mill, or empty string>"
}}

List at most three species candidates, most likely first. If the photo shows no log, say so
in "caution" and return empty candidate and defect lists."""


def analyze_log_photo(jpeg_bytes: bytes, species_hints: list[str]) -> dict[str, Any]:
    """Run one log photo through the first backend that answers.

    Returns the parsed suggestion dict plus a ``_backend`` key naming which
    endpoint produced it. Raises :class:`VisionUnavailable` with a message
    listing everything that was tried when no backend could answer -- callers
    surface that message verbatim, because "which server was down" is exactly
    what the operator needs to know.
    """
    prompt = PROMPT_TEMPLATE.format(species_list=", ".join(species_hints))
    image_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()

    attempts: list[str] = []
    backends = [("local", LOCAL_URL, "", LOCAL_MODEL)]
    if FALLBACK_URL:
        backends.append(("fallback", FALLBACK_URL, FALLBACK_KEY, FALLBACK_MODEL))

    for name, url, key, model in backends:
        if not url:
            continue
        try:
            raw = _post_chat(url, key, model, prompt, image_uri)
            result = _parse_response(raw)
            result["_backend"] = f"{name} ({url})"
            return result
        except Exception as exc:  # noqa: BLE001 - each backend failure is data
            attempts.append(f"{name} {url}: {exc}")
            logger.warning("Vision backend %s failed: %s", name, exc)

    raise VisionUnavailable(
        "No vision backend answered. Tried -- " + "; ".join(attempts) + ". "
        "Start the local model with app-manager-mcp/scripts/start-visual-debug-llama.ps1, "
        "or set LUMBER_VISION_FALLBACK_URL/_KEY for a hosted one."
    )


def _post_chat(url: str, key: str, model: str, prompt: str, image_uri: str) -> str:
    """One OpenAI-compatible chat call with an image. Returns the raw text."""
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_uri}},
                ],
            }
        ],
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    request = urllib.request.Request(
        url + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        body = json.loads(response.read())
    return str(body["choices"][0]["message"]["content"])


def _parse_response(raw: str) -> dict[str, Any]:
    """Extract and sanity-check the JSON object from the model's reply.

    Models wrap JSON in prose, code fences, and thinking tags no matter what
    the prompt says -- and thinking models put stray braces in their reasoning,
    so the first ``{`` is not necessarily the answer. Try each candidate
    object in turn until one parses; a reply with none is a backend failure,
    not a result.
    """
    data: dict[str, Any] | None = None
    search_from = 0
    while data is None:
        start = raw.find("{", search_from)
        if start < 0:
            raise ValueError(f"no JSON object in response: {raw[:120]!r}")

        depth = 0
        end = -1
        in_string = False
        escaped = False
        for i, ch in enumerate(raw[start:], start):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

        if end > 0:
            try:
                candidate = json.loads(raw[start:end])
                if isinstance(candidate, dict):
                    data = candidate
                    break
            except json.JSONDecodeError:
                pass  # a brace in prose, not an object -- keep looking
        search_from = start + 1

    candidates = []
    for item in data.get("species_candidates") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        candidates.append(
            {"name": str(item["name"])[:60], "confidence": round(confidence, 2),
             "why": str(item.get("why", ""))[:300]}
        )

    defects = []
    for item in data.get("defects") or []:
        if not isinstance(item, dict) or not item.get("type"):
            continue
        defects.append(
            {"type": str(item["type"])[:40],
             "severity": str(item.get("severity", ""))[:20],
             "note": str(item.get("note", ""))[:300]}
        )

    return {
        "species_candidates": candidates[:3],
        "defects": defects[:8],
        "milling_advice": str(data.get("milling_advice", ""))[:500],
        "caution": str(data.get("caution", ""))[:500],
    }
