import { useLayoutEffect, useRef, type ReactNode } from "react";
import { WarningCircle } from "@phosphor-icons/react";

/**
 * The form primitive the console did not have.
 *
 * Every state lives in `.field` in styles.css rather than in the components
 * that use it, so the six of them - rest, hover, focus, invalid, disabled and
 * read-only-because-a-run-is-in-flight - cannot drift apart between two
 * screens.
 *
 * The label is always rendered and always visible. A placeholder standing in
 * for a label disappears exactly when it is needed, which is while typing.
 */
export function Field({
  label,
  help,
  error,
  htmlFor,
  children,
}: {
  label: string;
  help?: ReactNode;
  error?: string | null;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="max-w-[68ch]">
      <label
        htmlFor={htmlFor}
        className="mb-1.5 block text-[11px] font-medium text-dim"
      >
        {label}
      </label>
      {children}
      {/* Help stays put rather than hiding in a tooltip. The rule it carries
          is the most common way to write a case that cannot pass. */}
      {help && !error && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-dim">{help}</p>
      )}
      {error && <FieldError message={error} />}
    </div>
  );
}

/**
 * Colour is never the only signal, and it is never the message either.
 * `--invalid` is Lc 59.6 - enough for a border and a glyph, short of the body
 * threshold - so the sentence that explains the mistake is set in `--text`.
 */
function FieldError({ message }: { message: string }) {
  return (
    <p className="mt-1.5 flex items-start gap-1.5 text-[11px] leading-relaxed text-text">
      <WarningCircle
        size={13}
        weight="fill"
        color="var(--invalid)"
        className="mt-[1px] shrink-0"
        aria-hidden
      />
      {message}
    </p>
  );
}

/**
 * A sentence, not a value.
 *
 * The instruction and the success condition are prose a person wrote, so they
 * are set in the sans face at a readable measure and the box grows to fit
 * rather than making someone scroll a four-line window. `--font-mono` is
 * reserved for coordinates and latencies, which line up in columns.
 */
export function TextArea({
  id,
  value,
  onChange,
  invalid,
  readOnly,
  rows = 2,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  invalid?: boolean;
  readOnly?: boolean;
  rows?: number;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      id={id}
      ref={ref}
      rows={rows}
      value={value}
      readOnly={readOnly}
      placeholder={placeholder}
      aria-invalid={invalid ? "true" : undefined}
      onChange={(e) => onChange(e.target.value)}
      className="field prose-field"
    />
  );
}

/**
 * A number with its unit inside the box.
 *
 * Budgets are data, so they get tabular figures and a 32px control rather than
 * the 40px the prose fields use. The unit sits in the field because a separate
 * label beside it would read as another column in a row that already has four.
 */
export function NumberField({
  id,
  label,
  value,
  unit,
  onChange,
  invalid,
  error,
  readOnly,
  min,
}: {
  id: string;
  label: string;
  value: number | string;
  unit?: string;
  onChange: (next: string) => void;
  invalid?: boolean;
  error?: string | null;
  readOnly?: boolean;
  min?: number;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1.5 block text-[11px] font-medium text-dim">
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type="number"
          inputMode="numeric"
          min={min}
          value={value}
          readOnly={readOnly}
          aria-invalid={invalid ? "true" : undefined}
          onChange={(e) => onChange(e.target.value)}
          className="field nums h-field-sm py-0"
          style={unit ? { paddingRight: `${unit.length * 0.62 + 1.4}em` } : undefined}
        />
        {unit && (
          <span
            className="nums pointer-events-none absolute inset-y-0 right-2.5 flex items-center text-[11px] text-faint"
            aria-hidden
          >
            {unit}
          </span>
        )}
      </div>
      {error && <FieldError message={error} />}
    </div>
  );
}
