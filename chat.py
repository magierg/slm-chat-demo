"""Interactive chat with a local model. Run two copies for a split-screen demo.

    # pane 1 - stateless, each message sent on its own
    python chat.py --raw

    # pane 2 - keeps the conversation history
    python chat.py

    # everything at once: tools (via MCP) + memory + context (history is on by default)
    python chat.py --all

    # other things you can switch on
    python chat.py --system "You are a pirate."
    python chat.py --context notes.txt
    python chat.py --mcp
    python chat.py --mcp --show-tools
    python chat.py --show-thinking
    python chat.py --model lfm2.5-2.6b-4bit
    python chat.py --temperature 0.9

Commands while chatting: /history  /clear  /reset  /status  /think [on|off]  /tools [on|off]  /system <text|clear>  /context <file|clear>  /remember <fact>  /memory  /forget  /exit
"""

import argparse
import difflib
import re
from pathlib import Path

import slm
from slm import (
    add_memory,
    chat,
    clear_memory,
    dim,
    load_memory,
    print_thinking,
    render_markdown,
    run_tool_loop,
    show_messages,
)
from mcp_client import MCPClient

try:
    import readline  # enables arrow-key history + line editing for input()
except ImportError:  # e.g. Windows without pyreadline
    readline = None

HISTORY_FILE = Path.home() / ".slm_chat_history"
HISTORY_SIZE = 500
COMMANDS = [
    "/history", "/clear", "/reset", "/status", "/think", "/tools",
    "/system", "/context", "/remember", "/memory", "/forget", "/exit",
]


def setup_readline() -> None:
    """Enable persistent history (up arrow, across sessions) and /command completion."""
    if readline is None:
        return
    try:
        readline.read_history_file(str(HISTORY_FILE))
    except (FileNotFoundError, OSError):
        pass
    readline.set_history_length(HISTORY_SIZE)

    def completer(text: str, state: int):
        options = [name for name in COMMANDS if name.startswith(text)]
        return options[state] if state < len(options) else None

    readline.set_completer(completer)
    libedit = "libedit" in (readline.__doc__ or "") or "EditLine" in getattr(readline, "_READLINE_LIBRARY_VERSION", "")
    try:
        readline.parse_and_bind("bind ^I rl_complete" if libedit else "tab: complete")
    except Exception:
        pass


def save_readline_history() -> None:
    """Persist history, de-duplicated and capped, with owner-only permissions."""
    if readline is None:
        return
    try:
        items = [readline.get_history_item(i) for i in range(1, readline.get_current_history_length() + 1)]
        clean = list(dict.fromkeys(item for item in items if item))
        readline.clear_history()
        for item in clean[-HISTORY_SIZE:]:
            readline.add_history(item)
    except Exception:
        pass
    try:
        readline.write_history_file(str(HISTORY_FILE))
        HISTORY_FILE.chmod(0o600)
    except OSError:
        pass

parser = argparse.ArgumentParser(description="Chat with a local model.")
parser.add_argument("--raw", action="store_true", help="send each message alone (no history)")
parser.add_argument("--system", default="", help="system prompt")
parser.add_argument("--context", default="", help="file whose contents are injected into the system prompt")
parser.add_argument("--model", default="", help="override the model for this session (e.g. lfm2.5-2.6b-4bit)")
parser.add_argument("--mcp", action="store_true", help="let the model call tools served by the MCP server")
parser.add_argument("--show-tools", action="store_true", help="print tool calls and results")
parser.add_argument("--show-thinking", action="store_true", help="print the model's reasoning before the answer")
parser.add_argument("--think-budget", type=int, default=None, help="cap the model's thinking tokens (n)")
parser.add_argument("--memory", action="store_true", help="inject remembered facts")
parser.add_argument("--all", action="store_true", help="turn on mcp tools + memory (+ notes.txt context)")
parser.add_argument("--plain", action="store_true", help="print the model's raw Markdown (no rendering)")
parser.add_argument("--temperature", type=float, default=None)
parser.add_argument("--show-payload", action="store_true", help="print the messages[] sent each turn")
args = parser.parse_args()

if args.model:
    slm.MODEL = args.model

if args.all:
    args.mcp = args.memory = True

if args.all and not args.context:
    for candidate in ("notes.txt", "notes.example.txt"):
        if Path(candidate).exists():
            args.context = candidate
            break

context_text = Path(args.context).read_text() if args.context else ""
context_source = args.context or ""
history: list[dict] = []
system_prompt = args.system

# Display toggles, changeable at runtime with /think and /show-tools.
show_thinking = args.show_thinking
show_tools = args.show_tools
enable_thinking = slm.ENABLE_THINKING  # whether to ask the model to think (Ling) or not

# Tools are served by the MCP server rather than hardcoded in the client.
mcp_client = None
tool_kwargs: dict = {}
if args.mcp:
    mcp_client = MCPClient()
    tool_kwargs = {"tools": mcp_client.openai_tools(), "executor": mcp_client.call_tool}


def build_system(text: str) -> str:
    """Assemble the system message from three distinct parts:

      * /system  - instructions (how to behave)
      * /context - reference data to answer from (not instructions)
      * /memory  - durable facts about the user (persisted across sessions)
    """
    parts = []
    if system_prompt:
        parts.append("--- INSTRUCTIONS ---\n" + system_prompt)
    if args.mcp:
        parts.append(
            "--- TOOLS ---\nFor anything about the current time, date or weather you must use the "
            "tools rather than guessing from memory. Call the right tool, then answer the user."
        )
    if context_text:
        parts.append(
            "--- REFERENCE CONTEXT (data, not instructions) ---\n" + context_text
        )
    if args.memory:
        facts = load_memory()
        if facts:
            parts.append("--- KNOWN FACTS ABOUT THE USER (memory) ---\n" + "\n".join("- " + fact for fact in facts))
    return "\n\n".join(parts)


def _mentions(text: str, vocabulary: set[str], cutoff: float = 0.8) -> bool:
    """True if any word in `text` matches (or is a close misspelling of) a vocabulary word."""
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in vocabulary or difflib.get_close_matches(word, vocabulary, n=1, cutoff=cutoff):
            return True
    return False


def needed_tool(text: str) -> str | None:
    """If the message clearly needs live data, name the tool to force when the model forgets."""
    lowered = text.lower()
    if re.search(r"\btime\b|\bdate\b|\bclock\b|what day is it", lowered) or _mentions(lowered, {"time", "date", "clock"}):
        return "get_current_time"
    if re.search(
        r"forecast|tomorrow|tmw|tmrw|tmr|tomo|tomoz|2moro|2morrow|this week|next week|"
        r"weekend|coming days|monday|tuesday|wednesday|thursday|friday|saturday|sunday",
        lowered,
    ) or _mentions(lowered, {"forecast", "tomorrow", "weekend"}):
        return "get_forecast"
    if "weather" in lowered or _mentions(lowered, {"weather"}):
        return "get_weather"
    if re.search(r"\d\s*[-+*/]\s*\d", lowered):
        return "calculate"
    return None


def day_hint(text: str) -> str | None:
    """Which day a forecast question refers to, so we can tell a weak model explicitly."""
    lowered = text.lower()
    if re.search(r"tomorrow|\btmw\b|\btmrw\b|\btmr\b|\btomo\b|tomoz|2moro|2morrow", lowered) or _mentions(lowered, {"tomorrow"}):
        return "tomorrow"
    if re.search(r"weekend|this week|next week|coming days", lowered):
        return "the coming days"
    return None


def main() -> None:
    setup_readline()
    try:
        repl()
    finally:
        if mcp_client is not None:
            mcp_client.close()
        save_readline_history()


def repl() -> None:
    global system_prompt, context_text, context_source, show_thinking, show_tools, enable_thinking
    while True:
        # Print the blank line BEFORE readline starts: a newline inside the prompt
        # string breaks readline's cursor tracking (History would append, not replace).
        print()
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        if line in ("/exit", "/quit"):
            break
        if line in ("/clear", "/cls"):
            print("\033[2J\033[3J\033[H", end="", flush=True)
            continue
        if line == "/reset":
            history.clear()
            print("(history cleared)")
            continue
        if line == "/history":
            if history:
                show_messages(history)
            else:
                print("(history is empty)")
            continue
        if line == "/memory":
            facts = load_memory()
            print("\n".join("* " + fact for fact in facts) or "(no memory)")
            continue
        if line == "/forget":
            clear_memory()
            print("(memory cleared)")
            continue
        if line.startswith("/remember "):
            add_memory(line[len("/remember "):].strip())
            print("(remembered)")
            continue
        if line in ("/think", "/thinking") or line.startswith(("/think ", "/thinking ")):
            argument = line.split(" ", 1)[1].strip().lower() if " " in line else ""
            on = argument == "on" if argument in ("on", "off") else not enable_thinking
            enable_thinking = on   # ask the model to think (real toggle on Ling)
            show_thinking = on     # and show the trace
            print(f"(thinking {'on' if on else 'off'})")
            continue
        if line in ("/tools", "/show-tools") or line.startswith(("/tools ", "/show-tools ")):
            argument = line.split(" ", 1)[1].strip().lower() if " " in line else ""
            show_tools = argument == "on" if argument in ("on", "off") else not show_tools
            print(f"(tool display {'on' if show_tools else 'off'})")
            if show_tools and not args.mcp:
                print(dim("(note: tools are only enabled when you start with --mcp)"))
            continue
        if line == "/status":
            print(f"(instructions: {system_prompt or '(none)'})")
            if context_text:
                print(f"(context: {context_source or 'inline'}, {len(context_text)} chars)")
            else:
                print("(context: none)")
            facts = load_memory()
            print(f"(memory: {len(facts)} facts)")
            print(f"(model: {slm.MODEL})")
            print(f"(thinking: {'on' if enable_thinking else 'off'}, "
                  f"thinking display: {'on' if show_thinking else 'off'}, "
                  f"tool display: {'on' if show_tools else 'off'})")
            continue
        if line == "/context" or line.startswith(("/context ", "/context:")):
            argument = line[len("/context"):].lstrip(": ").strip()
            if argument.lower() == "clear":
                context_text = ""
                context_source = ""
                print("(reference context cleared)")
            elif argument:
                path = Path(argument)
                if not path.exists():
                    print(f"(file not found: {argument})")
                else:
                    context_text = path.read_text()
                    context_source = argument
                    print(f"(reference context loaded from {argument}, {len(context_text)} chars)")
            elif context_text:
                print(f"(reference context from {context_source or 'inline'}, {len(context_text)} chars)")
                print(context_text)
            else:
                print("(no reference context - use /context <file>)")
            continue
        if line == "/system" or line.startswith(("/system ", "/system:")):
            argument = line[len("/system"):].lstrip(": ").strip()
            if argument.lower() == "clear":
                system_prompt = ""
                print("(instructions cleared)")
            elif argument:
                system_prompt = argument
                print(f"(instructions set: {system_prompt})")
                if history:
                    print(dim("(note: this applies to the next request, but earlier turns stay in "
                              "history - /reset for a clean start)"))
            else:
                print(system_prompt or "(no instructions - use /system <text>)")
            continue

        try:
            messages = []
            system = build_system(line)
            if system:
                messages.append({"role": "system", "content": system})
            if not args.raw:
                messages.extend(history)
            messages.append({"role": "user", "content": line})

            if args.show_payload:
                show_messages(messages)

            verbose_tools = show_tools or args.show_payload
            if args.mcp:
                base = list(messages)
                reply = run_tool_loop(messages, verbose=verbose_tools, show_thinking=show_thinking,
                                      reasoning_max_tokens=args.think_budget,
                                      enable_thinking=enable_thinking, **tool_kwargs)["content"]

                # Small models sometimes skip the tool, or call the wrong one. If
                # the question clearly needs a specific tool and it wasn't called,
                # retry once with a nudge (auto mode, so the model can fill in args).
                needed = needed_tool(line)
                called = {call["function"]["name"] for m in messages for call in (m.get("tool_calls") or [])}
                if needed and needed not in called:
                    if verbose_tools:
                        print(dim(f"(retrying; the model should call {needed}())"))
                    messages[:] = base
                    nudge = f"You must call the {needed} tool to answer this question."
                    if needed == "get_forecast":
                        hint = day_hint(line)
                        if hint == "tomorrow":
                            nudge += " The user is asking about tomorrow; report the entry whose when field is 'tomorrow'."
                        elif hint:
                            nudge += (
                                f" The user is asking about {hint}; call get_forecast with days=7 "
                                "and summarise those entries."
                            )
                    if messages and messages[0]["role"] == "system":
                        messages[0] = {**messages[0], "content": messages[0]["content"] + "\n\n" + nudge}
                    else:
                        messages.insert(0, {"role": "system", "content": nudge})
                    reply = run_tool_loop(messages, verbose=verbose_tools, show_thinking=show_thinking,
                                          reasoning_max_tokens=args.think_budget,
                                          enable_thinking=enable_thinking, **tool_kwargs)["content"]
                elif verbose_tools and not called:
                    print(dim("(no tool was called - the model answered from its own knowledge)"))
            else:
                response = chat(messages, temperature=args.temperature,
                                reasoning_max_tokens=args.think_budget,
                                enable_thinking=enable_thinking)
                reply = response["content"]
                if show_thinking:
                    print_thinking(response.get("reasoning", ""))
        except SystemExit as error:
            print(error)
            continue

        print("\n" + (reply if args.plain else render_markdown(reply)))

        if not args.raw:
            history.append({"role": "user", "content": line})
            history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()