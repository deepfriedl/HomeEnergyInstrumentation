"""A small local-model orchestration layer for the dashboard assistant."""

import json
import os
import urllib.error
import urllib.request

from app import analysis


OLLAMA_URL = os.getenv("ENERGY_OLLAMA_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("ENERGY_OLLAMA_MODEL", "qwen3:8b")
AGENT_ENABLED = os.getenv("ENERGY_AGENT_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
MAX_TOOL_ROUNDS = 3

SYSTEM_PROMPT = """You are Home Energy Analyst, a careful assistant for one private home.
Use the supplied read-only analytics tools for any factual claim about the home.
Never invent measurements, do not reveal device network configuration, and do not
offer to control plugs or other equipment. State the period analyzed and mention
coverage/errors when they could affect the conclusion. Treat correlation as an
observation, not proof of causation. Keep answers concise and practical."""


class AgentUnavailable(RuntimeError):
    """The local model is intentionally unavailable or cannot be reached."""


def configured():
    return AGENT_ENABLED


def _chat(messages):
    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": analysis.TOOL_SCHEMAS,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response).get("message", {})
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        raise AgentUnavailable("The local analysis model is unavailable. Check the Ollama service and model configuration.") from exc


def clean_history(history):
    """Keep only a short, plain-text client-side conversation context."""
    if not isinstance(history, list):
        return []
    cleaned = []
    for item in history[-8:]:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content", "")).strip()
        if content:
            cleaned.append({"role": item["role"], "content": content[:2400]})
    return cleaned


def answer(question, history=None):
    """Answer one dashboard question with at most three rounds of local tools."""
    if not configured():
        raise AgentUnavailable("Home Energy Analyst is not enabled yet.")
    question = str(question).strip()
    if not question or len(question) > 2400:
        raise ValueError("Ask one question of up to 2,400 characters.")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *clean_history(history), {"role": "user", "content": question}]
    sources = []
    for _ in range(MAX_TOOL_ROUNDS):
        message = _chat(messages)
        tool_calls = message.get("tool_calls") or []
        messages.append({key: value for key, value in message.items() if key in {"role", "content", "tool_calls"}})
        if not tool_calls:
            text = str(message.get("content") or "").strip()
            if text:
                return {"answer": text, "sources": sources, "model": OLLAMA_MODEL}
            raise AgentUnavailable("The local model returned no usable response.")
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "")
            result = analysis.run_tool(name, function.get("arguments", {}))
            sources.append({"tool": name, "period": result.get("period") if isinstance(result, dict) else None})
            messages.append({"role": "tool", "content": json.dumps(result, separators=(",", ":"))})
    raise AgentUnavailable("The model needed too many analysis steps. Please ask a narrower question.")
