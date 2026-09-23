# How the Android Runner Works

A walkthrough of the system for a demo audience: what it does, how one run
proceeds from first screenshot to final verdict, how a screenshot reaches the
vision model, and how every run is recorded on disk.

Roughly 4,200 lines of Python across sixteen modules, plus a React console.
No database, no orchestration framework, no agent library.

---

## 1. The one-paragraph version

You give it an instruction in English — *"Open the Test 14 chat and update the
audio metadata"* — and a **success condition** in English — *"the audio message
is labelled Verified Track with the artist Harness"*. It screenshots a real
Android device, sends the picture to a vision-language model, receives a tap or
a swipe back, performs it over ADB, screenshots again, and repeats. When the
work looks done, a **second, independent model call** looks at the final screen
and decides whether the success condition is actually true. Everything that
happened is written to a directory you can open.

The essential claim is narrow and worth stating plainly at a demo:

> The agent is never the one who decides whether it passed.

---

## 2. Separation of powers

There are two model roles, and keeping them apart is the design's centre of
gravity.

| | **The actor** | **The oracle** |
|---|---|---|
| Sees | Instruction, recent screenshots, its own past actions | One screenshot, one question |
| Knows | The task, its history | Nothing about the task or the agent |
| Prompt | `SYSTEM_PROMPT` — action space, swipe rules, app-opening rules | `ORACLE_SYSTEM_PROMPT` — no action space at all |
| Can do | Tap, swipe, type, wait, terminate | Answer `success` or `fail` |
| Decides the run | No | Yes |

They use the same served model, but the oracle gets a **fresh, history-free
context**. It never sees the instruction the actor was given, never sees what
the actor tried, and is never asked to reason about progress.

That separation was learned the hard way. The oracle originally ran under the
actor's system prompt, which ends:

> *Do not terminate with status success unless the requested task is complete
> on the screen in front of you.*

That sentence directly contradicts any grading question that asks the judge to
answer "yes" about a screen where the task is *not* complete — and the system
prompt won. Every grading pair collapsed to `(fail, fail)`. The oracle now
carries its own prompt with no action space, no advice about opening apps, no
swipe rules and no busy-screen rules. None of them belong to something that
only looks and answers. [`qwen_vl.py`]

### The two-question trick

A single yes/no question is a coin the model can flip. So the oracle asks
**two** questions about the same screenshot, in two separate fresh contexts:

1. **The predicate** — "Does the screen show that *{success}*?"
2. **The complement** — "Does the screen show something other than this:
   *{success}*?"

Both are phrased as plain positive questions. The answers **must disagree**. If
they agree, the model was not looking, and the verdict is thrown away as
`INCONCLUSIVE` rather than trusted.

This is why a healthy pass reads `condition: success / negation: fail` in the
console. That is the required disagreement, not a half-failure. (The key is
still named `negation` for a historical reason: the second question used to be
phrased as a double negative. Runs already on disk use the name, so it stayed.)

A verdict is **never retried**. Only a failed *transport* is. Asking again
until the answer changes is best-of-N dressed up as verification.
[`verification.py`]

---

## 3. One run, start to finish

This is the spine of the demo. Everything below happens in `runner.run()`.

### 3.1 Before the first screenshot

**Configuration** is read from the environment and an optional `.env`.
`ADB_DEVICE` is required — the runner refuses to guess which device to drive.

**Optional force-stop.** If `APP_PACKAGE` is set, the app is force-stopped so
the run starts cold. This is **opt-in and off by default**, and the reasoning is
a good demo anecdote: force-stopping by default cost more than it bought. Every
run then began on the launcher, the agent spent turn 0 just opening the app, and
the next screenshot caught it mid-draw. Note the difference from `pm clear`:
force-stop kills the processes but leaves storage alone, so the account stays
signed in. `pm clear` would hand every run a login screen.

### 3.2 The entry screenshot — and the settle loop

The first screenshot is not a single frame. `wait_until_settled` shoots
repeatedly until **two consecutive frames are visually still**, or a timeout
(`MODEL_SETTLE_TIMEOUT_S`, default 3s) expires.

This matters more than it sounds. It measures pixels, so it knows nothing about
any particular app: a cold start, a list still populating and a screen that was
already finished all read the same way. A fixed `sleep()` cannot do that — it is
a guess that is too long for a warm tap and too short for a cold start, and a
run that shows the model a half-drawn screen gets **correct taps aimed at the
wrong thing**.

The timeout is a normal exit, not an error. A spinner animates one tile forever,
so something has to end the wait. The record says which of the two happened:
`settled: true` or `settled: false`, plus `settle_ms`. A measured example from
disk: `settle_ms: 585.35, settled: true`.

### 3.3 Warm-up

One throwaway model call, carrying a **real screenshot** so it travels the same
downscale path and produces the same image-token count as a live turn.

This exists because the first call of a run otherwise absorbs model weight load,
`torch.compile` and CUDA graph capture. That is how the entry check once became
an unpaid warm-up that timed out doing the job. In a measured run, warm-up took
**13.4 seconds** while ordinary turns took ~5.7s.

### 3.4 The entry check — a guard, not a verdict

If a success condition was given, the oracle is asked **before any action**:
is this already true?

If yes, the run finishes immediately as `verified` with the detail *"success
condition held before any action"* — which is an honest and important result. It
means the test proved nothing about the agent, because the task was already
done.

If no — the normal case — the run proceeds. **An entry check that fails to
produce a usable answer does not stop the run.** It is a guard phase; only
`actor_claim`, `stuck` and `final` set the verdict. This is why you can see a
run marked green `verified` whose *first* checker says `inconclusive`. Nothing
went wrong.

### 3.5 The turn loop

Bounded by two separate budgets — `--max-actions` (default 20) and
`--max-waits` (default 12). **Nothing caps a run by elapsed time.** There used
to be a wall-clock deadline and it was the wrong bound: a case that legitimately
needs twenty minutes is not a case that has hung, and the deadline could not tell
them apart. What bounds the run is the action budget times the slowest call,
with `MODEL_TIMEOUT_S` and `ADB_TIMEOUT_S` bounding each individual call.

Each turn:

1. **Save the current screen** as `turn_NNN.png` — the screen the model is about
   to look at.
2. **Build the messages** (Section 4).
3. **Call the model.** Write the reply verbatim to `turn_NNN.raw.txt` before
   anything is parsed, so a parse failure still leaves the evidence.
4. **Parse** the reply into a thought, the reflection lines, a narration, and
   one tool call.
5. **Map coordinates** from the model's relative grid to device pixels.
6. **Resolve hold duration** if the action is a `long_press` (Section 5.7).
7. **Draw the marker** — a red crosshair on a copy of the screen the model was
   looking at, saved as `turn_NNN.marked.png`. Marked on the *before* screen, not
   the after, so the picture shows what it aimed at rather than where that left
   the app.
8. **Execute** over ADB.
9. **Settle and re-screenshot** as `turn_NNN.after.png`.
10. **Diff the two screens** — a 128×128 greyscale difference, both as a mean and
    as the worst 16px tile. A localized change is what counts: `moved` is true
    when any single tile exceeds 2.5%, which catches a menu opening in one
    corner that a whole-screen mean would average away.
11. **Write `turn_NNN.json`** and stream it to any listener.
12. **Compose the next turn's note** (Section 5.4).
13. **Check for repetition** (Section 5.8).

### 3.6 How a run ends

| Status | Meaning | Exit | `error_class` |
|---|---|---|---|
| `verified` | The oracle confirmed the success condition | 0 | `null` |
| `actor_gave_up` | The model terminated with `fail`, and the oracle agreed | 1 | `agent` |
| `stuck` | Repeated or oscillating actions | 1 | `agent` |
| `budget_exhausted` | Ran out of actions or waits | 1 | `agent` |
| `parse_error` | The reply could not be read | 1 | `agent` |
| `actor_claimed_success` | Claimed success with no condition to check | 1 | `agent` |
| `device_error` | ADB failed | 2 | `infrastructure` |
| `model_error` | The server failed | 2 | `infrastructure` |
| `oracle_error` | No verdict arrived — transport failed | 2 | `infrastructure` |
| `oracle_inconclusive` | A verdict arrived and was unusable | 2 | `oracle` |

**The three-way split is the point.** Exit code 1 means *the run measured the
agent and it did not get there*. Exit code 2 means *nothing was measured; this
result says nothing about the agent*. A timed-out oracle is not a failed task,
and reporting it as one makes every red result unreadable.

`oracle_error` and `oracle_inconclusive` are deliberately distinct. The first is
infrastructure: no verdict arrived, the transport failed, and the fix is your
server. The second is the judge: a reply arrived and was unusable, and the fix is
the grading prompt. Filing the second under infrastructure sent people to restart
a server that was working fine.

### 3.7 When the actor claims success

It does not end the run. The runner takes a fresh screenshot (`verify_NNN.png`)
and asks the oracle:

- **Oracle agrees** → `verified`.
- **Oracle disagrees** → the claim is counted as a spent action, the model is
  told *"Your success claim was rejected by the independent checker"*, and the
  run continues.
- **Oracle has no answer** → the run ends on the appropriate oracle status.

And a subtle rule worth mentioning: **an inconclusive oracle does not overwrite
a verdict the run had already reached.** A run that ended `stuck` keeps that
status and records the non-answer in `detail`. Only `oracle_error` — nothing
measured at all — replaces it. This was a real bug: a correct `stuck` detection
was once relabelled as a broken harness.

---

## 4. How a screenshot reaches the vision model

The part people ask about most.

### 4.1 Capture — raw bytes, never a file round-trip

```python
png = adb("exec-out", "screencap", "-p").stdout
```

`exec-out` returns the PNG on stdout as bytes. The bytes are validated
(`\x89PNG`), written to disk for the record, and carried onward **in memory**.
The image sent to the model is never re-read from disk.

### 4.2 Encoding — base64 data URL, OpenAI chat format

```python
encoded = base64.b64encode(png).decode("ascii")
{
  "role": "user",
  "content": [
    {"type": "image_url",
     "image_url": {"url": f"data:image/png;base64,{encoded}"}},
    {"type": "text", "text": label}
  ]
}
```

The image part is deliberately **first** in the content list. The history
trimmer identifies a screenshot message by its first content part, so keeping
the order fixed means it drops the whole message, caption included.

### 4.3 The message stack

A request is assembled as:

```
system    : the actor system prompt (action space, rules)
user      : the instruction
  ── for each remembered turn ──
user      : [IMAGE] "The screen you were looking at when you chose the action below."
assistant : what the model wrote that turn, minus its reasoning
  ──────────────────────────────
user      : [IMAGE] "The screen now. <note about what changed>"
```

Those two labels — `BEFORE_LABEL` and `NOW_LABEL` — are load-bearing. The model
sees several near-identical phone screenshots and cannot tell which is which
without being told.

### 4.4 History trimming, and a bug worth telling

`MODEL_HISTORY_N` (default 3) is a **total image count including the current
screenshot**, not a number of prior turns.

Old screenshots are dropped — but the assistant turns written about them are
**not** simply deleted. They are folded into a single `user` message:

> *"Actions you have already taken, oldest first. The screens you took them on
> are no longer shown."*

The earlier version dropped the image and left the bare `assistant` messages
behind. A 27-turn run put **24 consecutive `assistant` messages** into the
prompt, each describing a screen that was no longer in it, with no `user` turn
anywhere between them — a shape no chat template was ever trained on, and the
reason the model stopped following the format it was asked for.

Dropping them entirely is not an option either: erasing the record of what was
already tried is what makes an agent retry the same failing tap forever.

### 4.5 Downscaling — at the wire, not at capture

Just before the HTTP call, `wire.apply()` rewrites every message. Two things
happen:

**Tool-call tag escaping.** Ollama's parser scans *prompt* text for
`<tool_call>` tags even when the request declares no tools, and answers
`500 {"error":"EOF"}` when it finds them (ollama/ollama#14986). The system prompt
carries those tags and so does every replayed assistant turn. They are rewritten
to `[tool_call]...[/tool_call]` — line-for-line, same position, same JSON body —
and the parser reads either spelling back. Controlled by
`MODEL_ESCAPE_TOOL_CALLS`.

**Image downscaling to the training resolution.** `MAX_PIXELS = 1,003,520` — the
resolution the Qwen-family GUI models were trained at. Above it, **grounding
accuracy drops**. This is a correctness default, not an optimisation, which is
why `MODEL_MAX_IMAGE_PIXELS=0` means "use the model's own budget" rather than
"off". A negative value disables it.

Measured on a real frame from this repo:

| | Value |
|---|---|
| Device screenshot | 1080 × 2424 = 2,617,920 px |
| PNG on disk | 66,991 bytes |
| After downscale | 668 × 1500 = 1,002,000 px |
| PNG sent | 50,431 bytes |
| Base64 in the request | 67,244 characters |
| Measured prompt tokens | 3,059 |
| Completion tokens | 72 |
| Latency | 5,672 ms |

Note that the **full-resolution PNG stays on disk**. Only the copy on the wire is
shrunk.

### 4.6 The 0–1000 grid — why downscaling is safe

The model is told: *"The screen's resolution is 1000×1000. Coordinates you output
are relative on that 0-1000 grid, not device pixels."*

Because the answer is relative and the aspect ratio is preserved, **the
coordinate does not move when the image does**. The tap is then scaled against
the *original* screenshot size, not the shrunk copy.

A real example from `turn_001.json`:

```
model said : {"action": "click", "coordinate": [656, 932]}
executed   : {"action": "click", "coordinate": [708, 2259]}
```

`656 / 1000 × 1080 = 708.5 → 708`, `932 / 1000 × 2424 = 2259.2 → 2259`.

Out-of-range coordinates are rejected rather than clamped. A bounding box is
accepted as well as a point, and collapses to its centre.

---

## 5. Key points of the vision automation

The nine things worth calling out in a demo.

### 5.1 Pixels only — no accessibility tree

There is no `uiautomator dump`, no view hierarchy, no element IDs, no test hooks
in the app. The agent gets exactly what a person gets: a picture. That makes it
app-agnostic and framework-agnostic — React Native, Flutter, native, a WebView,
a game canvas all look the same to it — and it makes the result meaningful,
because nothing is being read out of a channel a user does not have.

The cost, stated honestly: the agent cannot see anything the screenshot does not
show, and it has no structural signal to tell a tutorial overlay from the screen
underneath it.

### 5.2 Relative coordinates

The model answers on a device-independent grid. One prompt works across screen
sizes, and the image can be resized freely on the way to the server.

### 5.3 Perception before action — the settle loop

Covered in 3.2. Probably the single highest-value mechanism in the system: most
"the model hallucinated" reports turn out to be the model correctly describing a
half-drawn screen.

### 5.4 The runner tells the model what it observed

After every action the next prompt carries a note:

> *"A localized screen change was detected after your previous action. That is
> not proof the action succeeded; check it against your previous Expect."*

or

> *"No localized screen change was detected after your previous action. Check
> that against your previous Expect."*

This is **measured ground truth from a pixel diff**, not the model's opinion, fed
back as an input. The careful phrasing matters: a change is evidence, not proof.

### 5.5 In-call reflection, not a second model call

With `MODEL_REFLECTION` on, each reply carries two extra one-line fields:

- **`Check:`** — did the last action do what I expected?
- **`Expect:`** — what must the screen show after the action I am choosing now?

They cost a few tokens inside a call the system was making anyway, rather than a
second round trip.

A nice detail: the `Check` line used to be stripped from replayed history on the
grounds that it judged an older step. That is what killed it — a model shown its
own prior turns, none of which carry a `Check`, writes no `Check` either. It
survived turn 0 and was gone by turn 1 in **every run on disk**. One Check-less
example was enough. Now it is replayed, and if it still goes missing the runner
says so explicitly in the next note.

Reasoning *is* still dropped from history — measured at 58% of a reply, and
unlike `Check` it is not addressed to the next turn. It is written to disk,
parsed and displayed regardless.

### 5.6 Grounding is auditable at a glance

The log already answers two thirds of *"did it tap the right thing"*: the
narration says what the model believed it was aiming at, and the coordinate says
where the tap went. What neither shows is whether those two **agree** — that
takes opening the screenshot and counting pixels by hand.

`turn_NNN.marked.png` draws the target on the image. A narration reading *"the
blue play button"* over a marker sitting in the message composer is a grounding
failure, and it looks like one immediately.

### 5.7 Hold duration is resolved, not guessed

A control that says *hold to confirm* needs a real long press; a tap on it does
nothing at all. The duration is resolved from up to four sources — the
instruction's own wording (*"hold for 3 seconds"*), the model's `duration_ms`,
the configured default, and the **device's own `long_press_timeout` setting**,
read once per run with a single shell call.

It is resolved *before* execution and written into the turn record with notes, so
the record states what happened rather than what was asked. It is deliberately
**not** clamped again at the ADB layer — a second clamp down there is what once
silently turned a three-second hold into a 200 ms tap and told nobody.

### 5.8 Repetition detection

Two rules over a coordinate key bucketed into a 32×32 grid:

- the same action three times in a row → `stuck`
- A-B-A-B oscillation → `stuck`

**Known limitation, worth being upfront about at a demo:** this keys only on the
action signature and ignores whether the screen changed. A tutorial overlay that
swallows three identical taps — while the screen changes more each time — is
scored as the agent being stuck. In one run on disk the screen deltas *rose*
each turn (0.052 → 0.130 → 0.227) and the third tap actually worked, and the run
was killed anyway. Gating the rule on the screen having gone quiet is the fix.

### 5.9 Determinism where it counts

`temperature = 0`. No SDK retries — they reapply the full timeout to every
attempt, so a call the runner capped at 180s could quietly take three times that.
Each call is one attempt inside the timeout the runner passes. Verdicts are
never retried; only failed transports are.

---

## 6. How runs are saved

### 6.1 The filesystem is the database

There is no database and no index. **A run directory is self-describing.**
Everything the console shows is recomputed by reading the directory.

This buys three things: a run can be opened with `cat` and `open`; it can be
zipped and mailed to someone with no software to install; and the console can be
restarted, replaced or removed without anything being lost.

### 6.2 Naming

A run id is a UTC timestamp to the second: `20260916T202644Z`. The format is
defined once in `runner.py` (`RUN_ID_STAMP`) because three parties depend on it —
the CLI creates it, the console creates it, and the reader parses it back into a
displayable time. Lexical order is therefore chronological, which is how the
console sorts the list without opening a single file.

Both entry points generate the id the same way. The console generates it
**before** spawning the child and passes it as `--out`, so it can hand the
browser a URL immediately.

### 6.3 What lands in a run directory

| File | Written | Contents |
|---|---|---|
| `case.json` | Before turn 0 | Snapshot of the case **as it was when run** |
| `entry.png` | Before turn 0 | The settled starting screen |
| `turn_NNN.png` | Start of turn | The screen the model was shown |
| `turn_NNN.raw.txt` | On reply | The model's reply, verbatim, before parsing |
| `turn_NNN.marked.png` | Before execute | Same screen with the target drawn on it |
| `turn_NNN.after.png` | After execute | The settled result |
| `turn_NNN.json` | End of turn | The full turn record |
| `verify_NNN.png` | On a success claim | The exact frame the oracle graded |
| `check_NNN.json` | Per oracle call | One verdict, both raw answers |
| `run.json` | At the end | The complete result, turns and checks embedded |
| `console.log` | Live, by the console | The child's stdout and stderr |

A worked example — a four-turn run occupies 26 files and 6.2 MB. Nineteen runs
on this machine total 240 MB. PNGs dominate; JSON is a rounding error.

**One deliberate duplication:** `turn_N.after.png` and `turn_N+1.png` are
byte-identical (verified by checksum). The same frame is both "the result of
what I did" and "what I am looking at now", and keeping both names means neither
view has to reason about the other's numbering.

### 6.4 Incremental writes — why `run.json` is not the only record

`run.json` is written **once, at the end**, by `finish()`. If that never
happens — a crash, a kill, a dead device — everything else is still there.

That is the whole reason `turn_NNN.json` and `check_NNN.json` are written
individually as the run proceeds. Two things depend on it:

- **Live streaming.** The console watches the directory. It does not parse the
  child's output and it does not hold a pipe; it reads files.
- **Crash survival.** A run that died before `finish()` is exactly the run worth
  looking at. The reader reconstructs a summary from the turn records rather than
  letting the directory vanish from the list.

### 6.5 The case snapshot

When a run comes from a saved case, the case is copied into the run directory
**before the first turn**. Two consequences:

- A run in progress already says what it is trying to do, before any result
  exists.
- Later edits to the stored case do not rewrite the history of what this run
  actually executed.

### 6.6 Reading it back

`RunStore` caches each summary against the run **directory's mtime** — which is
what changes when a run writes another artifact into it. `run.json` embeds every
turn, so listing twenty runs would otherwise mean re-reading every turn of every
one of them.

Two safety properties are worth showing:

- **Artifact serving is an allowlist, not a path join.** A filename is matched
  against a regex of exactly what the runner can write before it is joined to
  anything, and the resolved path is then checked to be a direct child of the run
  directory. Local-only is not a reason to serve a traversal.
- **Deletion is the one irreversible operation**, and it resolves and re-checks
  parentage before unlinking anything.

### 6.7 Outcome vocabulary: five on disk, four on screen

`error_class` on disk is the source of truth and has five states: `pass`,
`agent`, `infrastructure`, `oracle`, `incomplete`. The console groups them into
four buckets, because the five-value vocabulary is written for whoever is
debugging the harness, and the run list is read by whoever wants to know how the
runs went:

| Filter | Fed by |
|---|---|
| Passed | `pass` |
| Failed | `agent` |
| Infrastructure | `infrastructure` + `oracle` |
| Manually stopped | `incomplete` |

Nothing is lost — a run's own page still prints its exact status. `oracle` is
bucketed under Infrastructure rather than Failed on purpose: a judge that
answered and was unusable says nothing about the agent, and filing it under
Failed would blame the agent for our own parser.

A fifth word, **Running**, appears on runs the launcher reports as in flight. It
is a display state, not an outcome, and is not offered as a filter.

### 6.8 Starting a run from the console

A **subprocess, not a thread**. `model-run` drives a device over ADB, and an ADB
call that never returns would take the console down with it if it shared the
process. Out of process, a wedged run costs exactly one run.

The console refuses to start a second run while one is in flight. One emulator
cannot run two tests at once: they would each act on the other's screen, and both
records would be fiction. It is a refusal, not a queue — the caller decides
whether to wait.

The in-memory launch registry holds process state — a pid, an exit code — which
does not outlive the process that owns it. Everything worth keeping is already on
disk.

---

## 7. A suggested demo order

1. **Show a case.** One instruction, one success condition, two budgets.
2. **Start it and watch the console stream.** Point out that the console is
   reading files, not a pipe.
3. **Open a turn.** Screenshot, the model's own words, the action, the marked
   crosshair. This is the "it really is looking at pixels" moment.
4. **Open a checker.** Two questions, two answers, and the requirement that they
   disagree.
5. **Open a failed run.** Show that `stuck` is `agent` and `oracle_inconclusive`
   is not — the three-way split is the most credible thing in the system.
6. **Open the run directory in a file browser.** No database. That lands.

---

## 8. Honest limits

Worth saying out loud rather than being asked:

- **Repetition detection ignores screen change** (5.8), so an overlay that
  swallows taps is scored as an agent failure.
- **The parser is strict about the tool-call envelope.** The oracle intermittently
  emits a bare arguments object instead of the full `{"name": ..., "arguments":
  ...}` wrapper, and the reply is rejected even though its verdict was correct.
- **`APP_PACKAGE` is unset by default**, so app state carries from one run into
  the next. Good for speed, bad for isolation — and it is why a tutorial from an
  earlier run's signup can appear at the start of the next one.
- **Every screenshot is a full PNG in the prompt.** Cost grows with
  `MODEL_HISTORY_N`.
- **`adb input text` is printable ASCII only.** No emoji, no non-Latin input.
- **One device at a time.** There is no parallelism and no queue.
