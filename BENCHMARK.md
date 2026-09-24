# Benchmark

Measured latency and throughput for the local models this demo targets, on the
same machine, using the same harness (`benchmark.py`).

## Test machine

| | |
|---|---|
| Machine | Apple M4 MacBook Air (`Mac16,12`), 16 GB unified memory |
| OS | macOS 27.0 |
| Server | rapid-mlx 0.15.0 (`rapid-mlx serve <alias>`) |
| Client | Python 3.14.7 |

## How to run

One model per server (rapid-mlx serves a single model per port):

```bash
# dense, always-on reasoning
rapid-mlx serve lfm2.5-2.6b-4bit
python benchmark.py --model lfm2.5-2.6b-4bit --label "LFM2.5-2.6B dense"

# MoE reasoner
rapid-mlx serve ling-3.0-tiny-4bit
python benchmark.py --model ling-3.0-tiny-4bit --label "Ling-3.0-tiny MoE"
```

Options: `--runs N` (default 3), `--max-tokens N` (default 1024),
`--think-budget N` (cap reasoning), `--modes off,on`, `--base-url`, `--api-key`.

## Methodology

- Three fixed prompts (`short-math`, `explain`, `list`), each run 3 times, mean reported.
- `temperature 0`, `max_tokens 1024`, reasoning **uncapped** (no `--think-budget`).
- Streaming (SSE) so time-to-first-token can be measured.
- Token counts come from the response `usage`; `reason tok` from
  `completion_tokens_details.reasoning_tokens`.
- **TTFT reason** = first `reasoning_content` delta; **TTFT answer** = first
  `content` delta; **tok/s** = generated tokens / total; **decode tok/s** =
  generated tokens / (total − first token of any kind).

## Results

### LFM2.5-2.6B (dense) — measured

`model lfm2.5-2.6b-4bit | runs/cell: 3 | max_tokens: 1024`

| prompt | think | TTFT reason (s) | TTFT answer (s) | total (s) | gen tok | reason tok | tok/s | decode tok/s |
|---|---|---|---|---|---|---|---|---|
| short-math | off | n/a | 0.07 | 5.89 | 341 | 0 | 57.9 | 58.6 |
| short-math | on | 0.09 | 5.88 | 5.89 | 341 | 170 | 57.9 | 58.8 |
| explain | off | n/a | 0.09 | 3.84 | 221 | 0 | 57.5 | 59.0 |
| explain | on | 0.09 | 2.82 | 3.84 | 221 | 92 | 57.6 | 59.0 |
| list | off | n/a | 0.09 | 2.94 | 168 | 0 | 57.2 | 59.1 |
| list | on | 0.09 | 2.27 | 2.93 | 168 | 71 | 57.2 | 59.1 |

**Reading it:** ~58 tok/s decode. `think off` vs `on` produces the **same number
of tokens** and the same total time — the flag only changes whether the early
tokens are labelled `reasoning_content` (`on`) or folded into `content` (`off`).
LFM2.5-2.6B **always does the reasoning work**; disabling thinking does not save
compute here.

### Ling-3.0-tiny (MoE) — measured

`model ling-3.0-tiny-4bit | runs/cell: 3 | max_tokens: 1024`

| prompt | think | TTFT reason (s) | TTFT answer (s) | total (s) | gen tok | reason tok | tok/s | decode tok/s |
|---|---|---|---|---|---|---|---|---|
| short-math | off | n/a | 0.12 | 0.16 | 4 | 0 | 26.1 | 128.7 |
| short-math | on | 0.08 | 0.88 | 0.91 | 87 | 42 | 95.6 | 104.6 |
| explain | off | n/a | 0.08 | 0.53 | 48 | 0 | 91.4 | 106.6 |
| explain | on | 0.08 | 3.35 | 3.77 | 382 | 177 | 101.4 | 103.5 |
| list | off | n/a | 0.12 | 0.51 | 42 | 0 | 83.0 | 107.1 |
| list | on | 0.08 | 0.25 | 1.00 | 95 | 14 | 95.3 | 103.4 |

**Reading it:** ~90–105 tok/s decode — faster than the dense LFM despite being a
7.9B model, because only ~1.3B parameters are active per token. `enable_thinking`
is a **real** switch: `off` answers in ~0.1–0.5 s with a handful of tokens;
`on` adds a reasoning pass (42–177 reasoning tokens here).

## Which one to use

| | LFM2.5-2.6B (dense) | Ling-3.0-tiny (MoE) |
|---|---|---|
| decode speed | ~58 tok/s | **~90–105 tok/s** |
| thinking toggle | none (always reasons) | **real** (`enable_thinking`) |
| fast answers | ~3–6 s (must think) | **~0.1–0.5 s** with thinking off |
| reasoning quality | good | **stronger** (built for CoT/agents) |
| weights / RAM | ~2.5 GB | ~4.2 GB |
| tool parser | `lfm` | `glm47` (both work via MCP) |

**Recommendation: use `ling-3.0-tiny-4bit`.** On this machine it is both
**faster** (higher decode throughput) and **stronger** (better chain-of-thought),
and its thinking toggle lets you choose snappy answers (`/think off`) or deeper
reasoning (`/think on`). The only real cost is memory (~4.2 GB weights), which is
comfortable on 16 GB.

Keep `lfm2.5-2.6b-4bit` as a fallback for lower-memory machines or if you prefer a
dense model — but note it cannot actually skip reasoning, so it is never the
low-latency choice:

```bash
SLM_MODEL=ling-3.0-tiny-4bit ./run.sh --all     # recommended
SLM_MODEL=lfm2.5-2.6b-4bit ./run.sh --all       # fallback
```

## Observations

- **Throughput:** Ling (7.9B MoE, ~1.3B active) decodes ~90–105 tok/s here, vs
  ~58 tok/s for the dense 2.6B LFM. Sparse activation beats the smaller dense
  model despite far more total parameters.
- **Time to first answer** is dominated by how much the model thinks first. On
  Ling, `/think off` or `--think-budget` is the biggest latency lever; with
  thinking off it answers in ~0.1–0.5 s.
- **Reasoning costs tokens.** The `reason tok` column is often ~half the reply;
  it counts against `max_tokens` and against wall-clock time.
- **LFM's toggle is cosmetic.** `think off` vs `on` produces the same token count
  and time — the flag only relabels reasoning tokens as `content`. LFM always does
  the work, which is why it cannot be fast.
- **MoE trade-off:** Ling gains speed from sparse activation but still needs all
  ~4.2 GB of weights resident (memory follows *total* parameters, speed follows
  *active* parameters).

## Caveats

- Single machine, single run set; treat as indicative, not a leaderboard.
- rapid-mlx's **prefix cache** helps repeated identical prompts, so TTFT on the
  2nd/3rd run is flattered. Use fresh prompts for cold-start numbers.
- Token counts are the server's `usage`; the reasoning/content split in
  streaming can differ from non-streaming responses (observed on LFM).
- Numbers depend on thermals, background load, and the exact quant/alias.
