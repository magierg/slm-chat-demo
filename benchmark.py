"""Benchmark a local OpenAI-compatible model: latency, throughput, reasoning.

Measures, per prompt and per thinking mode:
  * TTFT-reason  - seconds to the first reasoning token
  * TTFT-answer  - seconds to the first answer token
  * total        - seconds for the whole reply
  * gen / reason - generated tokens, of which reasoning
  * tok/s        - generated tokens per second (end to end)
  * decode tok/s - tokens per second after the first answer token (pure decode)

Run it against one model at a time (rapid-mlx serves one model per port):

    python benchmark.py --model lfm2.5-2.6b-4bit --label "LFM2.5-2.6B dense (M4 Air 16GB)"
    python benchmark.py --model ling-3.0-tiny-4bit --label "Ling-3.0-tiny MoE (M4 Air 16GB)"

Results are printed as Markdown, ready to paste into BENCHMARK.md.
"""

import argparse
import json
import statistics
import sys
import time

import requests

import slm

PROMPTS = [
    ("short-math", "Reply with the single number: 17*23"),
    ("explain", "Explain why the sky is blue in two sentences."),
    ("list", "List three practical uses of Python, one short line each."),
]


def run_once(base_url, api_key, model, prompt, think, max_tokens, think_budget):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if think is not None:
        body["enable_thinking"] = think
    if think_budget:
        body["reasoning_max_tokens"] = think_budget

    start = time.perf_counter()
    ttft_reason = ttft_answer = None
    reasoning_chunks = content_chunks = 0
    usage = {}

    with requests.post(f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                       json=body, stream=True, timeout=300) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            if chunk.get("usage"):
                usage = chunk["usage"]
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                reasoning_chunks += 1
                if ttft_reason is None:
                    ttft_reason = time.perf_counter() - start
            if delta.get("content"):
                content_chunks += 1
                if ttft_answer is None:
                    ttft_answer = time.perf_counter() - start
    total = time.perf_counter() - start

    details = (usage.get("completion_tokens_details") or {})
    gen_tokens = usage.get("completion_tokens") or (reasoning_chunks + content_chunks)
    reason_tokens = details.get("reasoning_tokens") or reasoning_chunks
    first_token = ttft_reason or ttft_answer
    decode_s = total - first_token if first_token is not None else 0.0
    return {
        "ttft_reason": ttft_reason,
        "ttft_answer": ttft_answer,
        "total": total,
        "gen": gen_tokens,
        "reason": reason_tokens,
        "tok_s": gen_tokens / total if total else 0,
        "decode_tok_s": (gen_tokens / decode_s) if decode_s > 0.01 else float("nan"),
    }


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else float("nan")


def fmt(value, digits=2):
    return "n/a" if value is None or value != value else f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark a local model.")
    parser.add_argument("--model", default=slm.MODEL)
    parser.add_argument("--base-url", default=slm.BASE_URL)
    parser.add_argument("--api-key", default=slm.API_KEY)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--think-budget", type=int, default=None, help="cap thinking tokens (rapid-mlx)")
    parser.add_argument("--modes", default="off,on", help="comma list of thinking modes: off,on")
    parser.add_argument("--label", default=None, help="title for the results table")
    args = parser.parse_args()

    try:
        requests.post(f"{args.base_url}/chat/completions",
                      headers={"Authorization": f"Bearer {args.api_key}"},
                      json={"model": args.model, "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 4}, timeout=300)
    except requests.RequestException as error:
        raise SystemExit(f"Could not reach {args.base_url}: {error}")

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    title = args.label or args.model
    print(f"# Benchmark: {title}\n")
    print(f"model `{args.model}`  |  runs per cell: {args.runs}  |  max_tokens: {args.max_tokens}"
          + (f"  |  think_budget: {args.think_budget}" if args.think_budget else ""))
    print(f"base_url `{args.base_url}`\n")
    print("| prompt | think | TTFT reason (s) | TTFT answer (s) | total (s) | gen tok | reason tok | tok/s | decode tok/s |")
    print("|---|---|---|---|---|---|---|---|---|")

    for name, prompt in PROMPTS:
        for mode in modes:
            think = {"off": False, "on": True, "default": None}[mode]
            runs = [run_once(args.base_url, args.api_key, args.model, prompt, think,
                             args.max_tokens, args.think_budget) for _ in range(args.runs)]
            print(f"| {name} | {mode} "
                  f"| {fmt(mean([r['ttft_reason'] for r in runs]))} "
                  f"| {fmt(mean([r['ttft_answer'] for r in runs]))} "
                  f"| {fmt(mean([r['total'] for r in runs]))} "
                  f"| {mean([r['gen'] for r in runs]):.0f} "
                  f"| {mean([r['reason'] for r in runs]):.0f} "
                  f"| {fmt(mean([r['tok_s'] for r in runs]), 1)} "
                  f"| {fmt(mean([r['decode_tok_s'] for r in runs]), 1)} |")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
