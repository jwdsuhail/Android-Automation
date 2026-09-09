# Minimal Android Runner

A self-contained ADB + vision-model runner. This directory can be moved to a
different location or initialized as its own repository. It imports nothing
from the parent project.

## Design

- One command: `model-run`
- One autonomous loop
- Optional independent success check
- One wall-clock deadline
- Separate action and wait budgets
- Repeat/oscillation detection
- In-call Check/Expect reflection on the actor, not a second model call
- PNG, raw response, JSON turn records, and one `run.json`

## Layout

The split is model-specific versus model-agnostic, drawn once:

```text
src/android_runner/
  qwen_vl.py       prompt, messages, parsing, replay, coordinates
  client.py        OpenAI transport, the reasoning channel, diagnostics
  wire.py          tool-call tag escaping, screenshot downscaling
  runner.py        the loop
  verification.py  the independent checker
  device.py        ADB
  config.py cli.py
```

`qwen_vl.py` is named for the model on purpose. A second model means a second
file like it, not an abstraction layered over this one.

The actor and checker use the same configured model, but the checker gets a
fresh prompt containing only the human-written success condition and current
screenshot. It never sees the actor's instruction or history, and it never
asks for Check/Expect lines.

## Install

```bash
cd minimal_android_runner
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`, then confirm the device:

```bash
adb -s emulator-5554 get-state
```

## Verified run

```bash
model-run \
  --instruction "Open the Test 14 chat and update the audio metadata" \
  --success "the audio message is labelled Verified Track with the artist Harness" \
  --max-actions 70 \
  --max-waits 20 \
  --wall-clock-s 2400
```

Exit code `0` means the independent checker verified `--success`. Every other
outcome exits `1`; configuration refusal exits `2`.

The success condition must describe something visible on the final screen.
Split historical or multi-stage requirements into separate runs. A single
emulator cannot prove what another participant sees.

## Exploration run

```bash
model-run --instruction "Explore Settings" --max-actions 20
```

Without `--success`, an actor termination is recorded as
`actor_claimed_success`, `verified` remains false, and the command exits `1`.
This prevents an exploratory model claim from being mistaken for a QA pass.

## Serving on Ollama

Ollama's tool-call parser scans **prompt** text for `<tool_call>` tags even
when the request declares no tools, and answers `500 {"error":"EOF"}` when it
finds them ([ollama#14986](https://github.com/ollama/ollama/issues/14986); the
fix, [PR #15011](https://github.com/ollama/ollama/pull/15011), is still open).
The system prompt carries those tags and so does every replayed assistant turn,
so a run dies on the first request and would die on the second even with a
clean prompt.

```bash
MODEL_ESCAPE_TOOL_CALLS=1
```

That rewrites them to `[tool_call]…[/tool_call]` on the way out; the parser
reads either spelling back. Leave it off for vLLM or SGLang, which serve the
tags the model was trained on.

## Screenshots and grounding

Screenshots are downscaled to `qwen_vl.MAX_PIXELS` (1003520), the resolution
Qwen-family GUI models are trained at. Sending more pixels than that grounds
*worse*, so this is a default rather than an optimisation — a 1080×2424 capture
is 2.6× over budget. Aspect ratio is preserved and taps are scaled against the
**original** screenshot, so the relative 0–1000 grid still maps back correctly.
`MODEL_MAX_IMAGE_PIXELS` overrides it; a negative value disables downscaling.

## Thinking

Off by default. The reasoning is the larger half of a reply — measured at 58% —
and it never changes the next decision, so replayed history carries the action
and drops the reasoning. The full reply is still written to
`turn_NNN.raw.txt`, parsed, and shown.

`MODEL_THINKING=1` switches the prompt back on and stops sending
`reasoning_effort=none`. The no-think prompt is *derived* from `SYSTEM_PROMPT`
by string replacement rather than maintained as a second copy, so the two
cannot drift apart silently.

Each turn record carries `finish_reason` and `reasoning_chars`. The first
distinguishes a reply truncated at `max_tokens` from a model that refused to
act — both otherwise surface as `no tool_call block`. The second is how you
verify the thinking toggle was actually honoured by the server.

## Reflection

On by default. This is not thinking and not the independent `--success`
checker. At the start of turn N+1 the actor writes two short labelled lines
inside the same call:

```text
Check: whether the last action matched its Expect. Write "first step" on turn 0.
Expect: what the screen must show after the action chosen now.
Action: what it does now.
```

The prompt already contains the screen from before the last action (`BEFORE`)
and the screen after it (`NOW`). Replayed history keeps `Expect` and `Action`
and drops `Check`. `MODEL_REFLECTION=0` turns the lines off. Reflection
requires `MODEL_HISTORY_N` of at least 2 so that before/after pair is present.

A localized pixel change on the screenshot is fed back as a note. It is evidence
that something moved, not proof the action succeeded. No localized change is
evidence the last action had no visible effect; the actor still has to Check
that against its previous Expect. The independent oracle is the only path to
`verified`.

## Artifacts

Each run writes to `runs/<UTC timestamp>/` unless `--out` is supplied:

```text
entry.png
turn_000.png
turn_000.raw.txt
turn_000.json
turn_000.after.png
run.json
```

Verification replies are stored under `checks` in `run.json`.

## Tests

```bash
pytest
```

## Move it elsewhere

Move or copy the entire `minimal_android_runner` directory. Then recreate its
virtual environment and reinstall:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

No parent-repository path or package is referenced.
