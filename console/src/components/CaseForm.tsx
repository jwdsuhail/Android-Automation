import { useState } from "react";
import { ArrowLeft, ArrowDown, ArrowUp, FloppyDisk, Plus, X } from "@phosphor-icons/react";
import type { Case, CaseDraft, Checkpoint } from "../api";
import { Field, NumberField, TextArea } from "./Field";

const EMPTY: CaseDraft = {
  name: "",
  instruction: "",
  success: "",
  max_actions: 20,
  max_waits: 12,
  verify_timeout_s: null,
  warmup: true,
  checkpoints: [],
};

const NEW_STEP: Checkpoint = {
  id: "",
  condition: "",
  after: "",
  required: true,
};

/**
 * Mirrors `slugify()` in cases.py.
 *
 * Applied at save rather than while typing: slugifying each keystroke eats the
 * space between two words, so "Logged out" can never be typed at all.
 */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** A step as it is sent: the typed label becomes the id the ladder names. */
function settle(step: Checkpoint): Checkpoint {
  return {
    id: slugify(step.id),
    condition: step.condition.trim(),
    // Blank is not an empty pattern - that would match every turn and grade
    // the rung on the first screen of the run. It is "no trigger": asked once,
    // at the end.
    after: step.after?.trim() ? step.after.trim() : null,
    required: step.required,
  };
}

/** Per-row messages, keyed by position: a list cannot use one field's slot. */
type Errors = Partial<Record<keyof CaseDraft, string>> & {
  rows?: Record<number, string>;
};

/**
 * Mirrors `Case.validate()` in cases.py.
 *
 * The server is still the authority - it validates whatever arrives, whoever
 * sent it - but a rule you only learn about after a round trip is a rule you
 * learn about too late.
 */
function validate(draft: CaseDraft): Errors {
  const errors: Errors = {};
  if (!draft.name.trim()) errors.name = "Give the case a name so you can find it again.";
  if (!draft.instruction.trim()) {
    errors.instruction = "The instruction is what the agent is asked to do.";
  }
  if (!(draft.max_actions >= 1)) errors.max_actions = "At least 1.";
  if (!(draft.max_waits >= 1)) errors.max_waits = "At least 1.";
  if (draft.verify_timeout_s !== null && !(draft.verify_timeout_s > 0)) {
    errors.verify_timeout_s = "Greater than 0, or blank.";
  }

  const rows: Record<number, string> = {};
  const seen = new Set<string>();
  draft.checkpoints.forEach((step, index) => {
    const id = slugify(step.id);
    if (!id) {
      rows[index] = "Name the step, so the ladder can say which one was missed.";
    } else if (seen.has(id)) {
      rows[index] = `Another step is already called ${id}.`;
    } else if (!step.condition.trim()) {
      rows[index] = "Say what is visible on the screen once this step is done.";
    } else if (step.after && step.after.trim()) {
      try {
        new RegExp(step.after);
      } catch {
        // The server compiles it in Python, which is close but not identical.
        // Catching it here is about the bracket someone forgot, not parity.
        rows[index] = "That trigger is not a valid pattern.";
      }
    }
    seen.add(id);
  });
  if (Object.keys(rows).length > 0) errors.rows = rows;
  if (draft.checkpoints.length > 0 && !draft.success?.trim()) {
    errors.success =
      "Named steps qualify a pass, so they need a success condition to qualify.";
  }
  return errors;
}

export function CaseForm({
  existing,
  saving,
  serverError,
  onSave,
  onCancel,
}: {
  existing: Case | null;
  saving: boolean;
  serverError: string | null;
  onSave: (draft: CaseDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<CaseDraft>(() =>
    existing
      ? {
          name: existing.name,
          instruction: existing.instruction,
          success: existing.success ?? "",
          max_actions: existing.max_actions,
          max_waits: existing.max_waits,
          verify_timeout_s: existing.verify_timeout_s,
          warmup: existing.warmup,
          checkpoints: (existing.checkpoints ?? []).map((step) => ({
            ...step,
            after: step.after ?? "",
          })),
        }
      : EMPTY,
  );
  // Nothing is marked wrong until it has been left, or until a save is tried.
  // Reporting an empty required field while it is still being filled in is
  // the form telling someone off for not having finished typing.
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [attempted, setAttempted] = useState(false);

  const errors = validate(draft);
  const shown = (key: keyof CaseDraft) =>
    attempted || touched.has(key) ? errors[key] : undefined;

  const set = <K extends keyof CaseDraft>(key: K, value: CaseDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const leave = (key: string) =>
    setTouched((current) => new Set(current).add(key));

  const steps = draft.checkpoints;
  const rowError = (index: number) =>
    attempted || touched.has(`step-${index}`) ? errors.rows?.[index] : undefined;
  const setStep = (index: number, patch: Partial<Checkpoint>) =>
    set(
      "checkpoints",
      steps.map((step, at) => (at === index ? { ...step, ...patch } : step)),
    );
  const removeStep = (index: number) =>
    set(
      "checkpoints",
      steps.filter((_, at) => at !== index),
    );
  // Order is the order they are asked in only by accident - a rung fires when
  // its trigger matches - but it is the order the ladder is read in, so it is
  // worth being able to put the logout above the tutorial that follows it.
  const moveStep = (index: number, by: number) => {
    const to = index + by;
    if (to < 0 || to >= steps.length) return;
    const next = [...steps];
    [next[index], next[to]] = [next[to], next[index]];
    set("checkpoints", next);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setAttempted(true);
    if (Object.keys(errors).length > 0) return;
    // An omitted success condition is an exploration run. An empty string is
    // a typo, and the server refuses it, so it is normalised here instead.
    onSave({
      ...draft,
      success: draft.success?.trim() ? draft.success : null,
      checkpoints: draft.checkpoints.map(settle),
    });
  };

  return (
    // noValidate because `validate()` above is the authority, and it mirrors
    // Case.validate() on the server. Left on, the browser refuses the submit
    // on `min=1` before React sees it, which both suppresses every message
    // below and replaces them with a bubble in a typeface from no design
    // system here.
    <form onSubmit={submit} noValidate className="scroll h-full">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-panel px-5 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="row -ml-1 rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text"
            aria-label="Back"
          >
            <ArrowLeft size={15} weight="bold" aria-hidden />
          </button>
          <h2 className="truncate text-[13px] font-semibold">
            {existing ? existing.name : "New case"}
          </h2>
        </div>
        <button
          type="submit"
          disabled={saving}
          className="inline-flex h-field-sm items-center gap-1.5 rounded-xs bg-hover px-3 text-[12px] font-medium text-text transition-colors duration-150 hover:bg-border disabled:cursor-not-allowed disabled:text-faint"
        >
          <FloppyDisk size={14} weight="bold" aria-hidden />
          {saving ? "Saving" : "Save case"}
        </button>
      </header>

      <div className="space-y-6 px-5 py-5">
        <Field label="Name" htmlFor="name" error={shown("name")}>
          <input
            id="name"
            value={draft.name}
            onChange={(e) => set("name", e.target.value)}
            onBlur={() => leave("name")}
            aria-invalid={shown("name") ? "true" : undefined}
            placeholder="Audio metadata"
            className="field h-field"
          />
        </Field>

        <Field
          label="Instruction"
          htmlFor="instruction"
          error={shown("instruction")}
          help="What the agent is told to do. Naming a hold time in words - 'long press the MP3 for 3 seconds' - is what makes the device hold for that long."
        >
          <TextArea
            id="instruction"
            rows={3}
            value={draft.instruction}
            onChange={(next) => set("instruction", next)}
            invalid={Boolean(shown("instruction"))}
            placeholder="Open the Test 14 chat and update the audio metadata"
          />
        </Field>

        <Field
          label="Success condition"
          htmlFor="success"
          help="Verfication for the final state of the screen."
        >
          <TextArea
            id="success"
            rows={2}
            value={draft.success ?? ""}
            onChange={(next) => set("success", next)}
            placeholder="the audio message is labelled Verified Track with the artist Harness"
          />
        </Field>

        <fieldset className="max-w-[68ch]">
          <legend className="mb-1 text-[11px] font-medium text-dim">
            Named steps
          </legend>
          <p className="mb-3 text-[11px] leading-relaxed text-dim">
            Mid Step Verification.
          </p>

          {steps.map((step, index) => (
            <div
              key={index}
              className="mb-3 rounded-sm border border-border p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="nums text-[11px] text-faint">
                  Step {index + 1}
                </span>
                <div className="flex items-center gap-0.5">
                  <button
                    type="button"
                    onClick={() => moveStep(index, -1)}
                    disabled={index === 0}
                    className="rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text disabled:text-faint disabled:hover:bg-transparent"
                    aria-label={`Move step ${index + 1} up`}
                  >
                    <ArrowUp size={12} weight="bold" aria-hidden />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveStep(index, 1)}
                    disabled={index === steps.length - 1}
                    className="rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text disabled:text-faint disabled:hover:bg-transparent"
                    aria-label={`Move step ${index + 1} down`}
                  >
                    <ArrowDown size={12} weight="bold" aria-hidden />
                  </button>
                  <button
                    type="button"
                    onClick={() => removeStep(index)}
                    className="rounded-xs p-1 text-dim transition-colors duration-150 hover:bg-hover hover:text-text"
                    aria-label={`Remove step ${index + 1}`}
                  >
                    <X size={12} weight="bold" aria-hidden />
                  </button>
                </div>
              </div>

              <div className="mt-2 space-y-2.5">
                <input
                  value={step.id}
                  onChange={(e) => setStep(index, { id: e.target.value })}
                  onBlur={() => leave(`step-${index}`)}
                  aria-label={`Step ${index + 1} name`}
                  aria-invalid={rowError(index) ? "true" : undefined}
                  placeholder="Logged out"
                  className="field h-field-sm"
                />
                <TextArea
                  id={`step-condition-${index}`}
                  rows={2}
                  value={step.condition}
                  onChange={(next) => setStep(index, { condition: next })}
                  invalid={Boolean(rowError(index))}
                  placeholder="the login screen is visible, with no account signed in"
                />
                <input
                  value={step.after ?? ""}
                  onChange={(e) => setStep(index, { after: e.target.value })}
                  onBlur={() => leave(`step-${index}`)}
                  aria-label={`Step ${index + 1} trigger`}
                  placeholder="log ?out|sign out"
                  className="field nums h-field-sm"
                />
                <p className="text-[11px] leading-relaxed text-dim">
                  The trigger is matched against what the agent says it is
                  doing, not against a turn number: two runs of one case diverge
                  by the first turn and never line up again. Leave it blank to
                  check this step once, at the end.
                </p>
                <label className="flex cursor-pointer items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={step.required}
                    onChange={(e) =>
                      setStep(index, { required: e.target.checked })
                    }
                    className="mt-[2px] h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                  />
                  <span className="text-[12px] text-text">
                    A run that misses this step has not passed
                    <span className="mt-0.5 block text-[11px] leading-relaxed text-dim">
                      Clear it for a step that a run may legitimately miss - a
                      tip shown once and dismissed by the next tap. It is still
                      recorded, and it can no longer hold back a pass.
                    </span>
                  </span>
                </label>
                {rowError(index) && (
                  <p className="text-[11px] leading-relaxed text-text">
                    {rowError(index)}
                  </p>
                )}
              </div>
            </div>
          ))}

          <button
            type="button"
            onClick={() => set("checkpoints", [...steps, { ...NEW_STEP }])}
            className="inline-flex h-field-sm items-center gap-1.5 rounded-xs bg-hover px-3 text-[12px] text-text transition-colors duration-150 hover:bg-border"
          >
            <Plus size={13} weight="bold" aria-hidden />
            Add a step
          </button>
        </fieldset>

        <fieldset className="max-w-[68ch]">
          <legend className="mb-1 text-[11px] font-medium text-dim">Budgets</legend>
          <p className="mb-3 text-[11px] leading-relaxed text-dim">
            Whichever runs out first ends the run. Waits are counted separately
            from actions so that a slow screen cannot spend the whole budget.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <NumberField
              id="max_actions"
              label="Actions"
              min={1}
              value={draft.max_actions}
              onChange={(next) => set("max_actions", Number(next))}
              invalid={Boolean(shown("max_actions"))}
              error={shown("max_actions")}
            />
            <NumberField
              id="max_waits"
              label="Waits"
              min={1}
              value={draft.max_waits}
              onChange={(next) => set("max_waits", Number(next))}
              invalid={Boolean(shown("max_waits"))}
              error={shown("max_waits")}
            />
            <NumberField
              id="verify_timeout_s"
              label="Oracle timeout"
              unit="s"
              min={1}
              value={draft.verify_timeout_s ?? ""}
              onChange={(next) =>
                set("verify_timeout_s", next === "" ? null : Number(next))
              }
              invalid={Boolean(shown("verify_timeout_s"))}
              error={shown("verify_timeout_s")}
            />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-dim">
            Leave the oracle timeout blank to give the checker the same ceiling
            as the actor.
          </p>
        </fieldset>

        <label className="flex max-w-[68ch] cursor-pointer items-start gap-2.5">
          <input
            type="checkbox"
            checked={draft.warmup}
            onChange={(e) => set("warmup", e.target.checked)}
            className="mt-[2px] h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
          />
          <span className="text-[12px] text-text">
            Warm the model up first
            <span className="mt-0.5 block text-[11px] leading-relaxed text-dim">
              One throwaway call before the run, so no measured turn pays for
              weight load and graph capture. Measured at 13s against a 5.8s
              median.
            </span>
          </span>
        </label>

        {serverError && (
          <p
            className="max-w-[68ch] rounded-sm border px-3 py-2 text-[12px] leading-relaxed text-text"
            style={{ borderColor: "var(--invalid)" }}
            role="alert"
          >
            {serverError}
          </p>
        )}
      </div>
    </form>
  );
}
