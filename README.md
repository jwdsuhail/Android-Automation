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
  overlay.py       draws the action's target on the screenshot
  format.py        renders a turn record as one line of text
  cases.py         a saved task: the model-run arguments, named and stored
  config.py cli.py
  console/         optional local web console, see below
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

Add `console` to the extras if you want the web console: `pip install -e
".[dev,console]"`. The runner itself never imports it, so the core install
stays `openai` and `pillow`.

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

### Exit codes

| Code | Meaning | Statuses |
|------|---------|----------|
| `0` | The independent checker verified `--success`. | `verified` |
| `1` | The run measured the agent and it did not get there. | `parse_error`, `stuck`, `budget_exhausted`, `timed_out`, `actor_gave_up`, `actor_claimed_success` |
| `2` | Nothing was measured. The result says nothing about the agent. | `device_error`, `model_error`, `oracle_error`, `oracle_inconclusive`, configuration refusal |

`run.json` carries the same split as `error_class`: `"agent"`, `"infrastructure"`,
or `null` for a pass. A timed-out oracle is not a failed task, and reporting it
as one makes every red result unreadable.

The two infrastructure statuses that concern the checker are distinct on
purpose. `oracle_error` means no verdict arrived - the transport failed, and
the fix is your server. `oracle_inconclusive` means a reply arrived and was
unusable, most often the same answer to the success condition and its negation,
and the fix is the judge.

The oracle is retried only when the transport fails. A verdict is never
retried: asking again until the answer changes is best-of-N, not verification.
`checks[].attempts` and `checks[].errors` in `run.json` record what each verdict
cost.

### Cold start

Every run force-stops the app under test before it takes the entry screenshot:

```
adb -s $ADB_DEVICE shell am force-stop com.xuper.chat.app
```

That package is the default, so no flag or instruction has to ask for it. A run
that inherits the previous run's half-open dialog is measuring the last test as
much as this one, and that failure reads as flakiness rather than as leftover
state.

It is `am force-stop`, not `pm clear`: processes die and the task is dropped,
storage is untouched, and the account stays signed in. The run records what it
closed as `closed_app` in `run.json`, and the runner waits `MODEL_STEP_SLEEP`
afterwards so the entry screenshot catches the launcher rather than the closing
animation.

Set `APP_PACKAGE` to drive a different app, or to empty to leave the device
alone:

```bash
APP_PACKAGE=com.example.other model-run --instruction "..."
APP_PACKAGE= model-run --instruction "..."
```

A package that is not installed is not an error. `am force-stop` exits `0`
whatever name it is given, so a typo here closes nothing and says nothing;
check the name with `adb shell pm list packages | grep <name>`.

### Warm-up

The first call to a cold vision server pays for weight load, `torch.compile`
and CUDA graph capture - measured at 13s here against a 5.8s median. `model-run`
spends one throwaway call on that before the run starts, so no measured turn
absorbs it, and records the cost as `warmup_ms`. Pass `--no-warmup` to skip it.

`--verify-timeout-s` defaults to `MODEL_TIMEOUT_S`, the same ceiling the actor
gets.

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

## Saved cases

The arguments above are a test case: an instruction, the condition that proves
it worked, and the budgets that bound the loop. `--case` gives that set a name
and a file, so the same test can be the same test twice instead of a command
line retyped out of shell history.

```bash
model-run --case cases/audio-metadata.json
model-run --case cases/audio-metadata.json --max-actions 70   # the flag wins
```

One JSON file per case in `cases/`, which is the source of truth - no index and
no database, for the same reason `runs/` has neither:

```json
{
  "id": "audio-metadata",
  "name": "Audio metadata",
  "instruction": "Open the Test 14 chat and update the audio metadata",
  "success": "the audio message is labelled Verified Track with the artist Harness",
  "max_actions": 70,
  "max_waits": 20,
  "wall_clock_s": 2400,
  "verify_timeout_s": null,
  "warmup": true
}
```

Any flag given explicitly overrides the file. The one exception is
`--no-warmup`, which can only turn warm-up off and never back on, so a case
that stored `"warmup": false` stays off without it. `--instruction` is required
only when `--case` is absent.

`runs/` is ignored by git and `cases/` is not: a case is an input worth reading
in a diff, a run is output.

Cases are also written and started from the console below. Both paths validate
through `Case.validate()` in `cases.py`, so a case the browser rejects is
rejected at the terminal for the same reason and in the same words.

## Reading a step

Each turn prints what the model said, then what it actually did:

```text
[11] Long press the MP3 in the chat thread to open the context menu.
     expect: A context menu appears with an Edit option.
     -> long_press 500,812 of 1000 [bottom-center], px 540,1968, 1000ms no-change
```

The sentence is the model's *claim* about its target. Everything after `->` is
what was executed: the point on the model's relative 0-1000 grid, the ninth of
the screen it falls in, the device pixel it mapped to, and whether the screen
changed. The region is computed from the coordinate, not asked of the model, so
it cannot agree with a narration the tap contradicts - "tap the send button"
over `[bottom-left]` is a grounding failure you can see without opening a file.

`no-change` says the action did nothing, but not why. `turn_NNN.marked.png`
does, because it draws the target on the screen the model was choosing from.
Two turns of the run above both read `no-change` for opposite reasons: one
marker sits precisely on the blue play button, over a screen reading
"Compressing. Please wait.." - correct target, wrong moment - while turn 11's
sits on the audio waveform, a scrubber that swallows a long press, instead of
the message body that owns the context menu.

## Hold time

Write the duration into the instruction and it is the duration the device
holds for:

```bash
model-run --instruction 'Long press the MP3 in the "Test 14" chat for 3 seconds'
```

`for 3 seconds`, `for 800ms` and `for 2.5s` all read. The verb nearest the
duration decides who it belongs to, so `wait for 10 seconds then long press the
MP3` is a ten second wait and leaves the hold alone. Two *different* stated
hold times cannot both be enforced from one instruction, so neither is: the
model chooses per turn and the run record says why.

When the instruction names no time, the model's `duration_ms` stands, falling
back to `ADB_LONG_PRESS_MS` (default 1000).

Two bounds are applied, and both announce themselves:

- **Floor** - the device's own `long_press_timeout`, read once per run with
  `settings get secure long_press_timeout` (400ms on an SDK 35 emulator, 500ms
  assumed when the device will not say). Below it Android delivers a tap, so a
  shorter request is raised rather than quietly turned into a click.
- **Ceiling** - `ADB_TIMEOUT_S` less a two second margin, 28000ms by default.
  `adb shell input swipe` blocks for the whole hold, so a longer one would kill
  its own call. Raising `ADB_TIMEOUT_S` raises this with it.

Every adjustment lands in `turn_NNN.json` and on the terminal:

```text
     -> long_press 500,812 of 1000 [bottom-center], px 540,1968, 3000ms from instruction
     hold: instruction asked for 3s
     hold: model asked for 800ms, overridden by the instruction
```

```json
"hold": {
  "ms": 3000,
  "source": "instruction",
  "requested_ms": 3000,
  "model_ms": 800,
  "notes": ["instruction asked for 3s",
            "model asked for 800ms, overridden by the instruction"]
}
```

Qwen's own `mobile_use` schema spells this parameter `time`, in **seconds**,
shared with `wait`; this prompt asks for `duration_ms`, in **milliseconds**, on
`long_press` alone. So `duration_ms: 3` meaning three seconds is the mistake
the model is primed to make, and unrepaired it is a 3ms hold - a tap, while
every artifact still reads `3`. Such a value is read as seconds and the repair
is recorded, so the mistakes can be counted. A `duration_ms` that is not a
positive number ends the run as `parse_error`, the agent's fault, rather than
surfacing from inside ADB as a dead device.

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
turn_000.marked.png
turn_000.raw.txt
turn_000.json
turn_000.after.png
run.json
```

Two more appear conditionally. `case.json` is written before the first turn
when the run came from `--case`: the case exactly as it was when run, so
editing it afterwards does not rewrite history. `console.log` holds the
runner's stdout and stderr when the console started it, which is where to look
when a run dies before writing any turn at all.

Verification replies are stored under `checks` in `run.json`. A `long_press`
turn also carries a `hold` block naming the duration executed, who decided
it, and every adjustment made on the way - see [Hold time](#hold-time).

`turn_NNN.marked.png` is `turn_NNN.png` with the action's target drawn on it: a
crosshair for a tap or long press, an arrow for a swipe or drag. Actions with no
place on the screen - `type`, `wait`, `system_button`, `terminate` - write no
marked copy rather than a duplicate of the screenshot.

## Console

A local web console for reading runs and for writing and running cases.

```bash
pip install -e ".[console]"
npm --prefix console install
npm --prefix console run build
model-console                 # http://127.0.0.1:8765
```

It is not read-only. It reads `runs/`, it reads and writes `cases/`, it can
spawn one `model-run` at a time as a subprocess, and it can delete a run
directory. It never edits a run - the only change it will make to one is to
remove it whole - and there is no stop button, so a run started here is stopped
the way any other process is.

It binds `127.0.0.1` and has no authentication, which remains the whole of its
threat model. Anything that can reach the port can edit a case, start a run on
your emulator, and delete results. `--no-run` omits both endpoints that write
to `runs/` - starting and deleting - which leaves that directory read-only;
cases stay editable. `--runs-dir`, `--cases-dir` (default `cases/`) and
`--port` move the rest.

### Runs

Three columns: every run on the left, the chosen run's turns in the middle,
the chosen turn's screenshot and numbers on the right. The selected run and
turn live in the URL, so `/runs/20260914T121427Z?turn=11` is a link you can
send to someone rather than a place you have to describe.

The action line - `click 228,640 of 1000 [mid-left], px 246,1551 moved` - is
rendered by `format.py` on the server and sent as a string, so the browser and
the terminal cannot drift apart. See [Reading a step](#reading-a-step).

Runs are sorted into four outcomes, each with a glyph and a word as well as a
colour:

| Outcome | Means |
|---|---|
| Verified | the oracle confirmed the success condition |
| Not reached | the agent did not get there: `error_class` is `agent` |
| Infrastructure | something broke, including `oracle_inconclusive` |
| Unfinished | no `run.json`, reconstructed from the `turn_*.json` files |

The last row matters more than it looks. `run.json` is written only when a run
reaches the end, so a crashed or interrupted run leaves nothing but its turns -
and those are often the runs worth reading. The console rebuilds them rather
than hiding the directory.

The outcome dropdown filters the list; the count beside each outcome is of the
whole directory, not of what is on screen. **Select** turns the list into
checkboxes and offers a delete, which removes the run directories outright -
screenshots, turn records and all. There is no trash: `runs/` is ignored by
git, so a deleted run has no other copy anywhere. A run that is still being
written is refused by name rather than deleted out from under the process
writing it.

### Cases

The Cases tab lists what is in `cases/` and edits it. Saving writes the JSON
file described in [Saved cases](#saved-cases) and nothing else; deleting
removes that file. A file that will not parse is listed with its error rather
than skipped, on the same principle as an unfinished run. A case keeps its id
when you rename it, so the runs that recorded it still point at something.

**Run** starts `model-run --case` as a subprocess and streams the run as it
happens: turns append with their screenshots, and two meters show the action
and wall-clock budgets being spent, which are the numbers that decide whether
the run dies. A second Run while one is in flight is refused rather than
queued - one emulator cannot run two tests at once without each acting on the
other's screen - and the refusal names the run already going so you can go and
watch it.

Nothing parses the runner's output. The stream re-reads the run directory, for
the same reason the Unfinished outcome exists: a run in progress is just an
unfinished run. Two things follow. A run started at the terminal streams into
the browser as well as one started here, and reloading mid-run reattaches from
the directory instead of losing it.

Below 1024px the screenshot column is not shown; this is a desktop tool.

For development, `npm --prefix console run dev` serves on :5173 with hot
reload and proxies `/api` to :8765, so `model-console` has to be running too.

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

No parent-repository path or package is referenced. The console's
`node_modules` and its build output are not copied by git; rebuild them with
`npm --prefix console install && npm --prefix console run build` if you want
it, or leave them out entirely.
