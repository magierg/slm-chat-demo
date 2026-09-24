# slm-chat-demo

A tiny, **grab-and-run** chat client for a **local small language model**, built
for live, split-screen demos. Run two copies side by side: one talking to the
raw model, one with history, system prompt, context or tools switched on. Type
the same thing in both and watch the answers diverge.

One dependency: [`requests`](https://pypi.org/project/requests/). No frameworks,
no build step, no SDK hiding the HTTP.

## Setup

Requires **Python 3.8+** and a local model server.

The easiest route is the launcher, which creates the virtualenv and installs
dependencies on first run, then always uses the right Python - no activating,
no remembering to `pip install`:

```bash
cp .env.example .env
cp notes.example.txt notes.txt     # optional: your own context for --context / --all
./run.sh --all
```

Or set it up manually:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

> Do not delete `.venv` while a terminal has it activated - your `PATH` will
> point at a missing `python`. `./run.sh` avoids the whole issue.

**LM Studio** - load a model, start the local server, then set in `.env`:
```
SLM_BASE_URL=http://localhost:1234/v1
SLM_MODEL=<the model id shown in LM Studio>
SLM_API_KEY=lm-studio
```

**Ollama** - `ollama serve`, `ollama pull llama3.2:3b`, and the defaults already work.

## Usage

```bash
# pane 1 - raw / stateless: every message is sent on its own
./run.sh --raw

# pane 2 - keeps the conversation history
./run.sh

# everything at once: history (always on) + tools + memory + notes.example.txt
./run.sh --all
```

Or, with the venv activated, `python chat.py ...` works the same way. Type,
press Enter, get a reply. `/exit` quits.

> If `python` is not found, you have not activated the virtualenv - just use
> `./run.sh`, which handles it for you.

### Switches (mix and match per pane)

| Flag | What it adds |
|------|--------------|
| `--raw` | send each message alone (no history) |
| `--system "..."` | a system prompt (persona / rules) |
| `--context notes.example.txt` | inject a file's contents into the system prompt |
| `--mcp` | let the model call the tools served by the MCP server (`get_weather`, `get_forecast`, `calculate`, `get_current_time`) |
| `--show-tools` | print tool calls and results (hidden by default) |
| `--show-thinking` | print the model's reasoning trace before the answer |
| `--think-budget N` | cap the model's thinking tokens (overrides `SLM_REASONING_MAX_TOKENS`) |
| `--memory` | inject facts remembered from earlier sessions |
| `--all` | shorthand for `--mcp --memory`, plus `notes.example.txt` if present |
| `--plain` | print the model's raw Markdown instead of rendering it |
| `--temperature 0.9` | sampling temperature |
| `--show-payload` | print the exact `messages[]` (and rough token count) each turn |

### In-chat commands

```
/history            show the messages that will be sent next turn
/clear              clear the terminal screen
/reset              clear the conversation history
/status             show instructions, context, memory and the display toggles
/think [on|off]     show/hide the model's reasoning (no arg toggles)
/tools [on|off]     show/hide tool calls and results (no arg toggles)
/system <text>      set the instructions; /system shows, /system clear removes
/context <file>     load reference context; /context shows, /context clear removes
/remember <fact>    save a fact to persistent memory
/memory             list saved facts
/forget             clear saved memory
/exit               quit
```

`/system` changes the system message on the next request, but the existing
conversation stays in history - so a small model may blend the old and new
personas. `/reset` clears history for a clean switch. Check it with
`--show-payload` (the `SYSTEM` line changes immediately).

The `--show-thinking` and `--show-tools` flags just start with those displays on;
`/think` and `/tools` toggle them live, so you can reveal reasoning or tool calls
mid-conversation.

### system vs context vs memory

Three different things, all carried in the system message, clearly labelled so
`--show-payload` shows the difference:

```
--- INSTRUCTIONS ---                     <- /system   (how to behave)
--- TOOLS ---                            <- --mcp
--- REFERENCE CONTEXT (data, not instructions) ---   <- /context  (what to answer from)
--- KNOWN FACTS ABOUT THE USER (memory) ---          <- /memory   (what is known about you)
```

| | `/system` | `/context` | `/memory` |
|---|---|---|---|
| Purpose | Instructions, persona, rules | Reference data to answer from | Durable facts about the user |
| Authored by | You | You | You (`/remember`) |
| Scope | Session | Session | **Cross-session** |
| Stored | in-process | in-process (from a file) | `data/memory.json` |
| Survives restart | No | No | **Yes** |
| Survives `/reset` | Yes | Yes | Yes |

- **Instructions** say *how* to respond; **context** is *data* to respond from;
  **memory** is *facts about you* carried between sessions.
- Context is labelled "data, not instructions" - a nod to prompt-injection defense:
  the model is told to treat it as material, not rules.
- `--context` / `--system` / `--memory` are the launch-time equivalents of the
  commands; `--all` turns on `--mcp --memory` and loads `notes.txt` (or
  `notes.example.txt` if you have not made your own) as context.

## Split-screen demo ideas

Run both panes, then type the same lines in each:

- **Stateless vs history** - `chat.py --raw` vs `chat.py`.
  *"My favourite colour is purple."* then *"What is my favourite colour?"*
  Left draws a blank; right answers *purple*.

- **No system prompt vs system prompt** - `chat.py` vs `chat.py --system "You are a pirate."`
  Ask *"Explain APIs."*

- **No context vs context** - `chat.py` vs `chat.py --context notes.example.txt`.
  A sample `notes.example.txt` is included; the right pane answers from it.

- **No tools vs tools** - `chat.py` vs `chat.py --mcp`.
  Ask *"What time is it?"* The left guesses; the right calls `get_current_time()` over MCP.

- **Add `--show-payload`** in either pane to reveal the raw `messages[]` array
  being sent - the whole point being that "memory", "context" and "intelligence"
  are just messages, instructions and tool results.

## Configuration

Read from `.env` if present, then real environment variables.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible base URL |
| `SLM_MODEL` | `llama3.2:3b` | Model name/tag |
| `SLM_API_KEY` | `ollama` | Sent as `Authorization: Bearer ...` (ignored locally) |
| `SLM_TEMPERATURE` | `0.7` | Default sampling temperature |
| `SLM_MAX_TOKENS` | `4096` | Max **generated** tokens per reply (thinking + answer) |
| `SLM_REASONING_MAX_TOKENS` | *(unset)* | Optional cap on the thinking portion only (rapid-mlx) |

Smaller models are faster; bigger models follow instructions and call tools more
reliably. `chat.py` works with any of them.

## The HTTP request & response

Every turn is a single `POST` to the OpenAI-compatible chat endpoint. `chat.py`
never talks to the model directly - it calls `slm.chat()` (`slm.py`), which builds
the request and parses the response. Nothing is hidden behind an SDK.

```
POST {SLM_BASE_URL}/chat/completions
Authorization: Bearer {SLM_API_KEY}
Content-Type: application/json
```

**Request body** (built in `slm.chat()`):

```json
{
  "model": "lfm2.5-2.6b-4bit",
  "messages": [
    {"role": "system", "content": "--- INSTRUCTIONS --- ... --- REFERENCE CONTEXT --- ..."},
    {"role": "user", "content": "what's the weather tomorrow?"}
  ],
  "temperature": 0.7,
  "max_tokens": 4096,
  "reasoning_max_tokens": 512,
  "stream": false,
  "tools": [ /* only with --mcp: the tool schemas discovered over MCP */ ],
  "tool_choice": "auto"
}
```

| Field | Comes from | Meaning |
|-------|-----------|---------|
| `model` | `SLM_MODEL` | Which model to run |
| `messages` | system + history + user | The **entire** conversation; the model is stateless |
| `temperature` | `SLM_TEMPERATURE` / `--temperature` | Randomness (0 = deterministic) |
| `max_tokens` | `SLM_MAX_TOKENS` | Cap on **generated** tokens (see below) |
| `reasoning_max_tokens` | `SLM_REASONING_MAX_TOKENS` / `--think-budget` | Optional cap on the **thinking** part only (omitted if unset) |
| `stream` | fixed `false` | We wait for the whole reply; no SSE streaming |
| `tools` | MCP `tools/list` | Functions the model may call |
| `tool_choice` | fixed `auto` | The model decides whether to call a tool |

**Response body** (parsed in `slm.chat()`):

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Tomorrow in Wellington: light drizzle, high 13.8C.",
      "reasoning_content": "The user wants tomorrow's weather, so I should call get_forecast...",
      "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "get_forecast", "arguments": "{\"city\":\"Wellington\",\"days\":2}"}}
      ]
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 42, "completion_tokens": 123, "total_tokens": 165,
    "completion_tokens_details": {"reasoning_tokens": 40}
  }
}
```

| Field | Used for |
|-------|----------|
| `choices[0].message.content` | The final answer |
| `choices[0].message.reasoning_content` | The thinking trace (`--show-thinking`) |
| `choices[0].message.tool_calls` | If present, run them and POST again with the results appended |
| `choices[0].finish_reason` | `stop` = done, `length` = hit `max_tokens` (raise it) |
| `usage.prompt_tokens` / `completion_tokens` | Input vs generated size |
| `usage.completion_tokens_details.reasoning_tokens` | How much of the reply was thinking |

**The tool round-trip.** When the model returns `tool_calls`, `slm.run_tool_loop()`
appends the assistant's tool-call message and a `tool` result message to `messages`
and POSTs again. It repeats until the model replies without requesting a tool, then
asks for the final answer with `tools` omitted. That loop is the whole "agent".

### `max_tokens` explained

`max_tokens` is the maximum number of tokens the model may **generate** in its
reply - it does **not** limit the input. Three things matter:

- **Thinking counts too.** For reasoning models (LFM2.5), the `<think>` trace and
  the answer share this budget. Set it too low and the answer is cut off
  (`finish_reason: "length"`, e.g. a truncated `"17 *"`). The default is 4096.
- **Input is bounded separately** by the model's context window (128K for LFM2.5),
  i.e. how big `messages` can get.
- **Rule of thumb:** ~4 characters per token. A 4,096-token budget is roughly
  3,000 words of output.

It is an upper bound, not a target - the model still stops when it is done, so a
generous value costs nothing on normal replies and only prevents truncation.

**Want predictable latency?** Cap just the thinking with `reasoning_max_tokens`
(`SLM_REASONING_MAX_TOKENS`, or `--think-budget N` on the command line). That
server-specific parameter bounds only the `<think>` portion, so you can keep
`max_tokens` generous for the answer while reasoning stays short:

```bash
python chat.py --show-thinking --think-budget 256
```

Watch the real numbers in the response `usage` block (`completion_tokens` plus
`reasoning_tokens`), and raise `SLM_MAX_TOKENS` if you see `finish_reason: "length"`.

## MCP (Model Context Protocol)

[MCP](https://modelcontextprotocol.io) is an open protocol for exposing tools (and
resources/prompts) to AI applications. Instead of hardcoding tools inside the app,
you run a **server** that advertises them, and the app is a **client** that
discovers and calls them. Any MCP-compatible host (Claude Desktop, Cursor, ...) can
then use the same server.

Here, the local model does **not** speak MCP - `chat.py` is the bridge:

```
chat.py (host + MCP client)                         mcp_server.py (MCP server)
        |                                                    |
        |  spawn: python mcp_server.py                       |
        |--------------------------------------------------->|
        |  initialize                                        |
        |--------------------------------------------------->|
        |  tools/list                                        |
        |--------------------------------------------------->|
        |<-------------- [{name, description, inputSchema}]  |
        |                                                     |
        |  (convert to the OpenAI `tools` schema)             |
        |                                                     |
        |  POST /chat/completions {messages, tools}           |
        |     -> local model replies with tool_calls          |
        |                                                     |
        |  tools/call {name, arguments}                      |
        |--------------------------------------------------->|
        |<-------------- [{type:"text", text:"..."}]         |
        |                                                     |
        |  POST /chat/completions {messages + tool result}    |
        |     -> local model writes the final answer          |
```

```bash
./run.sh --mcp --show-tools
```

See the protocol work without the model:

```bash
python mcp_client.py          # starts the server, lists tools, calls a couple
```

- `mcp_server.py` - the server. JSON-RPC 2.0 over stdio, no dependencies, exposing
  `get_weather`, `get_forecast`, `calculate`, `get_current_time`. It reuses
  `slm.py` for the tool implementations. Methods implemented:
  `initialize`, `tools/list`, `tools/call`, plus the `notifications/initialized`
  notification.
  ```bash
  echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp_server.py
  ```
- `mcp_client.py` - the client `chat.py` uses to spawn the server, convert its
  tools to the OpenAI `tools` schema, and forward the model's tool calls.

Because it is a real MCP server, you can point other MCP hosts at it too.

## How the model chooses a tool

The model never sees MCP, HTTP, or your Python code - it only sees a list of tool
definitions in the request and decides by prediction. The steps:

1. **We advertise the tools.** Each tool is sent as a JSON schema: `name`,
   `description`, and the `parameters` the arguments must satisfy. These come from
   `tools/list` in MCP and are converted in `mcp_client.openai_tools()`.
2. **The model chooses, based on the descriptions.** It is not a router or an `if`
   statement - the model predicts which tool (if any) fits the user's request by
   matching intent against the tool **names and descriptions**, the system prompt,
   and the conversation so far. This is why the descriptions matter so much:
   "current live weather" vs "daily forecast" is what makes *"tomorrow"* land on
   `get_forecast`.
3. **`tool_choice` sets how strict it is** (`slm.py` `chat()`):
   - `"auto"` (our default) - the model decides whether and what to call.
   - `"none"` - no tools.
   - `"required"` - must call some tool.
   - `{"type":"function","function":{"name":"get_weather"}}` - force one.
4. **The server runtime parses the output.** Small models are trained to emit a
   special tool-call format; the inference server turns that raw text into
   structured `tool_calls` (your model advertises `tool_call_parser`).
5. **We execute and feed the result back.** `run_tool_loop()` sends the arguments
   to the MCP server (`tools/call`), appends the returned text as a `tool` message,
   and asks the model to write the final answer - with `tools` omitted here, so it
   writes in its normal voice instead of just dumping data.

Two honesty notes:

- **The model can choose wrong or not at all.** A 1-2B model may answer from its own
  head or pick the wrong tool. `chat.py` has a heuristic safety net
  (`needed_tool()`) that, when the question clearly needs a specific tool that
  wasn't called, retries once with a nudge naming it. That nudge is *our* code, not
  the model's reasoning - a larger model calls tools on its own more reliably.
- **Descriptions are the real lever.** Clear, distinct tool descriptions do more
  for correct selection than anything else. Fewer overlapping tools also helps.

## How it is organised

- `chat.py` - the interactive client (arg parsing, the REPL, `/commands`).
- `run.sh` - launcher that sets up/uses `.venv` for you.
- `slm.py` - the shared helpers, in five sections:
  1. **config** - `.env` + environment variables
  2. **chat** - builds the `/chat/completions` request and parses `content`, `reasoning_content`, `tool_calls`
  3. **printing** - `show_messages()`, Markdown rendering, token estimate
  4. **memory** - load/save facts in `data/memory.json`
  5. **tools** - tool schemas, `execute_tool()`, `run_tool_loop()`
- `mcp_server.py` / `mcp_client.py` - the MCP server and the client bridge.
- `notes.example.txt` - a sample context file. Copy it to `notes.txt` (git-ignored)
  and edit with your own details; use it with `--context` / `--all`.

## Notes on the design

- **Reasoning models** (like LFM2.5) think before answering. The server returns
  that trace separately as `reasoning_content`, and `--show-thinking` prints it
  dimmed above the answer. Thinking counts against `SLM_MAX_TOKENS` (default 4096),
  so keep it generous or answers get truncated; cap just the thinking with
  `--think-budget N` / `SLM_REASONING_MAX_TOKENS`.
- **Tool calling uses the native `tools` API and real weather data** from
  [Open-Meteo](https://open-meteo.com) (no API key, needs internet). Tools:
  `get_weather`, `get_forecast`, `calculate`, `get_current_time`. Tool calls are
  hidden unless you pass `--show-tools`. See
  [How the model chooses a tool](#how-the-model-chooses-a-tool) for the mechanics,
  including the fuzzy-matching nudge for weak models (typos like `tommorow`,
  shorthand like `tmw`).
- **Persona survives tool use.** Once a tool has run, the final answer is requested
  without `tools` offered, otherwise small models drop the system persona and just
  dump facts.
- **Memory persists** in `data/memory.json` (git-ignored) across runs. `/remember`
   writes a fact; `--memory` injects the stored facts into the prompt. Delete the
   file (or `/forget`) to start over.
- **Markdown is rendered** in the terminal: `**bold**`, `*italic*` and `` `code` ``
  become ANSI styles; when output is piped the markers are stripped. Use `--plain`
  to see the model's raw text.
- `--mcp` deliberately does **not** put the date in the system prompt, so
  *"what time is it?"* can only be answered by calling the tool.

### Using the official `openai` SDK instead

Only `slm.py` would change; `chat.py` stays identical. Replace the body of
`chat()` with:

```python
from openai import OpenAI

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

def chat(messages, temperature=None, tools=None):
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE if temperature is None else temperature,
        tools=tools or None,
    )
    message = response.choices[0].message
    return {
        "content": message.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in (message.tool_calls or [])
        ],
    }
```

`requests` is used here on purpose: seeing the raw JSON payload is the lesson.
