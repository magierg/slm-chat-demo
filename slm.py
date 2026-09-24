"""Shared helpers for chat.py.

Everything lives here, in six short sections:

    1. config   - environment / .env settings
    2. chat     - one call to the model (the interesting part)
    3. printing - showing the exact payload sent
    4. memory   - facts persisted outside the conversation
    5. tools    - functions the model is allowed to ask us to run
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ModuleNotFoundError:
    raise SystemExit(
        "\nThe 'requests' package is not installed.\n"
        "Activate your virtualenv and install it first:\n"
        "  source .venv/bin/activate   (Windows: .venv\\Scripts\\activate)\n"
        "  pip install -r requirements.txt\n"
    )

# =============================================================================
# 1. CONFIG
# =============================================================================
# Reads a plain `.env` file if one exists, then falls back to real environment
# variables, then to defaults. No python-dotenv needed.

ROOT = Path(__file__).parent
MEMORY_FILE = ROOT / "data" / "memory.json"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")

BASE_URL = os.environ.get("SLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
MODEL = os.environ.get("SLM_MODEL", "llama3.2:3b")
API_KEY = os.environ.get("SLM_API_KEY", "ollama")
TEMPERATURE = float(os.environ.get("SLM_TEMPERATURE", "0.7"))

# Total generated tokens (thinking + answer). Generous so answers aren't truncated.
MAX_TOKENS = int(os.environ.get("SLM_MAX_TOKENS", "4096"))

# Optional cap on the thinking portion only (server-specific, e.g. rapid-mlx).
# Leave unset for no cap; a modest value keeps reasoning fast and predictable.
_reasoning_cap = os.environ.get("SLM_REASONING_MAX_TOKENS", "").strip()
REASONING_MAX_TOKENS = int(_reasoning_cap) if _reasoning_cap else None

# Whether to ask the model to think before answering (rapid-mlx `enable_thinking`).
# Ling defaults reasoning OFF, so it needs this; LFM ignores it (always thinks).
# Set SLM_ENABLE_THINKING= (empty) to omit the field on servers that reject it.
_thinking = os.environ.get("SLM_ENABLE_THINKING", "true").strip().lower()
ENABLE_THINKING = None if _thinking == "" else _thinking not in ("0", "false", "no", "off")


# =============================================================================
# 2. CHAT - the core function
# =============================================================================

_THINK = re.compile(r"<think\b[^>]*>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _split_thinking(content: str) -> tuple[str, str]:
    """Separate any inline <think>...</think> from the answer. Returns (answer, thinking)."""
    match = _THINK.search(content)
    if not match:
        return content, ""
    return _THINK.sub("", content).strip(), match.group(1).strip()


def chat(messages: list[dict], temperature: float | None = None, tools: list[dict] | None = None,
         tool_choice=None, reasoning_max_tokens: int | None = None,
         enable_thinking: bool | None = None) -> dict:
    """Send a list of messages to the model and return {'content', 'reasoning', 'tool_calls'}.

    This is where the whole "stateless" idea becomes concrete: the *only* thing
    the model knows is what is inside `messages`. There is no session, no
    server-side memory. Send a different list, get a different answer.

    Reasoning models think before answering; the server puts that trace in
    `reasoning_content`, separate from the final answer. `enable_thinking` asks the
    model to think (Ling defaults off; LFM ignores it); `reasoning_max_tokens`
    optionally caps only the thinking portion. Both fall back to the
    `SLM_ENABLE_THINKING` / `SLM_REASONING_MAX_TOKENS` config.
    """
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    cap = REASONING_MAX_TOKENS if reasoning_max_tokens is None else reasoning_max_tokens
    if cap is not None:
        body["reasoning_max_tokens"] = cap
    think = ENABLE_THINKING if enable_thinking is None else enable_thinking
    if think is not None:
        body["enable_thinking"] = think

    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=body,
            timeout=300,
        )
    except requests.RequestException as error:
        raise SystemExit(
            f"\nCould not reach {BASE_URL}. Is your local model server running?\n"
            f"  Ollama:    ollama serve   (then: ollama pull {MODEL})\n"
            f"  LM Studio: start the local server and load a model\n"
            f"  ({error})"
        )

    if not response.ok:
        raise SystemExit(f"\nModel request failed ({response.status_code}): {response.text}")

    message = response.json()["choices"][0]["message"]
    content, inline_thinking = _split_thinking(message.get("content") or "")
    reasoning = message.get("reasoning_content") or inline_thinking
    return {
        "content": content,
        "reasoning": reasoning,
        "tool_calls": message.get("tool_calls") or [],
    }


def warmup() -> None:
    """Preload the model so the first real request isn't slow."""
    try:
        chat([{"role": "user", "content": "hi"}], temperature=0)
    except SystemExit:
        pass


# =============================================================================
# 3. PRINTING
# =============================================================================

def _color(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def dim(text): return _color("2", text)
def cyan(text): return _color("36", text)


def section(text: str) -> None:
    print("\n" + cyan("-- " + text))


def tool_call(name: str, arguments, result: str) -> None:
    print(f"  {cyan('tool')} {name}({json.dumps(arguments)}) -> {result}")


_MD_FENCE = re.compile(r"```[a-zA-Z0-9]*\n?")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!\w)_([^_\n]+)_(?!\w)")
_MD_HEADING = re.compile(r"(?m)^#{1,6}\s*")


def render_markdown(text: str) -> str:
    """Make the model's Markdown readable in a terminal.

    On a TTY, emphasis becomes ANSI bold/italic and inline code becomes cyan;
    when piped, the markers are simply stripped so logs stay clean.
    """
    text = _MD_FENCE.sub("", text)
    if not sys.stdout.isatty():
        text = _MD_INLINE_CODE.sub(r"\1", text)
        text = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
        text = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), text)
        return _MD_HEADING.sub("", text)

    text = _MD_INLINE_CODE.sub(lambda m: cyan(m.group(1)), text)
    text = _MD_BOLD.sub(lambda m: _color("1", m.group(1) or m.group(2)), text)
    text = _MD_ITALIC.sub(lambda m: _color("3", m.group(1) or m.group(2)), text)
    return _MD_HEADING.sub("", text)


def estimate_tokens(messages: list[dict]) -> int:
    """Rough token count (~4 chars/token). Good enough to show context growing."""
    characters = sum(len(message.get("content") or "") for message in messages)
    return max(1, characters // 4)


def show_messages(messages: list[dict]) -> None:
    """Print the exact payload being sent. This is the teaching tool."""
    tokens = estimate_tokens(messages)
    section(f"request payload: messages[] ({len(messages)})  ~{tokens} tokens")
    for message in messages:
        role = message["role"].upper().ljust(9)
        content = (message.get("content") or "").replace("\n", "\n" + " " * 11)
        print(f"  {cyan(role)}{content}")
        for call in message.get("tool_calls") or []:
            function = call["function"]
            print(f"  {cyan('TOOL_CALL')} {function['name']}({function['arguments']})")


def print_thinking(reasoning: str) -> None:
    """Print the model's reasoning trace, dimmed, above the answer."""
    if not reasoning.strip():
        return
    print("\n" + dim("  [thinking]"))
    for line in render_markdown(reasoning).splitlines():
        print(dim("  " + line))


# =============================================================================
# 4. MEMORY - durable facts, stored outside the transcript
# =============================================================================

def load_memory() -> list[str]:
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text())
        return [item for item in data if isinstance(item, str)]
    except (json.JSONDecodeError, OSError):
        return []


def save_memory(facts: list[str]) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(facts, indent=2) + "\n")


def add_memory(fact: str) -> list[str]:
    facts = load_memory()
    if fact and fact not in facts:
        facts.append(fact)
    save_memory(facts)
    return facts


def clear_memory() -> None:
    save_memory([])


# =============================================================================
# 5. TOOLS - functions the model may ask us to run
# =============================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current live weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name, e.g. Berlin"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": (
                "Get a daily weather forecast for a city. The first entry is today, "
                "the second is tomorrow, and so on. Use days=2 for tomorrow, days=7 for the week."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name, e.g. Wellington"},
                    "days": {"type": "integer", "description": "Number of days, 3 to 7 (default 3)"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a basic arithmetic expression, e.g. '12 * 8 + 4'.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "Arithmetic to evaluate"}},
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": (
                "Get the current local date and time. Always call this when the user "
                "asks for the time or date; never guess it."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes used by Open-Meteo.
_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains", 80: "slight rain showers", 81: "moderate rain showers",
    82: "violent rain showers", 85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _geocode(city: str) -> dict | None:
    geo = requests.get(
        _GEOCODE_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=15,
    )
    geo.raise_for_status()
    matches = geo.json().get("results") or []
    return matches[0] if matches else None


def get_weather(city: str) -> dict:
    """Live weather from Open-Meteo (free, no API key). Geocode the city, then fetch it."""
    try:
        place = _geocode(city)
        if not place:
            return {"city": city, "error": "city not found"}

        response = requests.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "timezone": "auto",
            },
            timeout=15,
        )
        response.raise_for_status()
        current = response.json()["current"]

        return {
            "city": f"{place.get('name')}, {place.get('country')}",
            "temperature_c": current["temperature_2m"],
            "wind_kmh": current["wind_speed_10m"],
            "conditions": _WEATHER_CODES.get(current["weather_code"], f"code {current['weather_code']}"),
        }
    except requests.RequestException as error:
        return {"city": city, "error": f"weather lookup failed: {error}"}


def get_forecast(city: str, days: int = 3) -> dict:
    """Daily forecast from Open-Meteo (today first, then upcoming days).

    Always returns at least 3 days so "tomorrow" and "this week" have data to
    answer from, even if the model asks for fewer.
    """
    try:
        days = max(3, min(int(days), 7))
    except (TypeError, ValueError):
        days = 3

    try:
        place = _geocode(city)
        if not place:
            return {"city": city, "error": "city not found"}

        response = requests.get(
            _FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": days,
                "timezone": "auto",
            },
            timeout=15,
        )
        response.raise_for_status()
        daily = response.json()["daily"]

        forecast = []
        for index, date in enumerate(daily["time"]):
            code = daily["weather_code"][index]
            when = {0: "today", 1: "tomorrow"}.get(index, f"in {index} days")
            forecast.append({
                "when": when,
                "date": date,
                "conditions": _WEATHER_CODES.get(code, f"code {code}"),
                "high_c": daily["temperature_2m_max"][index],
                "low_c": daily["temperature_2m_min"][index],
                "precip_chance": daily["precipitation_probability_max"][index],
            })

        return {"city": f"{place.get('name')}, {place.get('country')}", "forecast": forecast}
    except requests.RequestException as error:
        return {"city": city, "error": f"forecast lookup failed: {error}"}


def execute_tool(name: str, arguments: dict) -> str:
    """The real code the model can trigger. This runs locally, in your process."""
    if name == "get_weather":
        city = str(arguments.get("city", "")).strip()
        return json.dumps(get_weather(city))
    if name == "get_forecast":
        city = str(arguments.get("city", "")).strip()
        return json.dumps(get_forecast(city, arguments.get("days", 3)))

    if name == "get_current_time":
        return json.dumps({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    if name == "calculate":
        expression = str(arguments.get("expression", ""))
        if not set(expression) <= set("0123456789+-*/(). "):
            return json.dumps({"error": "unsupported expression"})
        try:
            return json.dumps({"expression": expression, "result": eval(expression)})
        except Exception:
            return json.dumps({"error": "could not evaluate expression"})

    return json.dumps({"error": f"unknown tool: {name}"})


def run_tool_loop(messages: list[dict], max_steps: int = 6, verbose: bool = True,
                  tools: list[dict] | None = None, executor=None, show_thinking: bool = False,
                  reasoning_max_tokens: int | None = None,
                  enable_thinking: bool | None = None) -> dict:
    """Ask the model; if it requests tools, run them and feed the results back.

    Keeps going until the model answers without requesting a tool. `messages` is
    modified in place so the caller keeps the full transcript.

    `tools` and `executor` default to the built-in ones, but can be supplied from
    elsewhere (e.g. an MCP server) to expose a different toolset. When
    `show_thinking` is set, the reasoning trace is printed at each step.
    """
    tool_list = TOOLS if tools is None else tools
    run_tool = executor or execute_tool

    result = {"content": "", "reasoning": "", "tool_calls": []}
    used_tools = False
    last_tool_output = ""
    for _ in range(max_steps):
        result = chat(messages, temperature=0, tools=tool_list,
                      reasoning_max_tokens=reasoning_max_tokens, enable_thinking=enable_thinking)
        if not result["tool_calls"]:
            if used_tools:
                # Prefer a personality-preserving synthesis WITHOUT tools offered,
                # but never return an empty answer: fall back to the with-tools
                # reply, then to a nudge, then to the raw tool output.
                final = chat(messages, temperature=0, reasoning_max_tokens=reasoning_max_tokens,
                             enable_thinking=enable_thinking)
                if not final["content"].strip():
                    final = result
                if not final["content"].strip():
                    nudge = messages + [{"role": "user",
                                         "content": "Now answer the user's question using the tool result above."}]
                    final = chat(nudge, temperature=0, reasoning_max_tokens=reasoning_max_tokens,
                                 enable_thinking=enable_thinking)
                if not final["content"].strip():
                    final = {"content": last_tool_output or "(no answer)",
                             "reasoning": "", "tool_calls": []}
                if show_thinking:
                    print_thinking(final.get("reasoning", ""))
                return final
            if show_thinking:
                print_thinking(result.get("reasoning", ""))
            return result

        if show_thinking:
            print_thinking(result.get("reasoning", ""))
        used_tools = True
        messages.append({"role": "assistant", "content": result["content"], "tool_calls": result["tool_calls"]})
        for call in result["tool_calls"]:
            function = call["function"]
            try:
                arguments = json.loads(function["arguments"])
            except json.JSONDecodeError:
                arguments = {}
            output = run_tool(function["name"], arguments)
            last_tool_output = output
            if verbose:
                tool_call(function["name"], arguments, output)
            messages.append({
                "role": "tool",
                "content": output,
                "tool_call_id": call["id"],
                "name": function["name"],
            })
    return result