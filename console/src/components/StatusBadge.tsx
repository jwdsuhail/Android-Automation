import type { Outcome } from "../api";
import { OUTCOME, bucketOf } from "../format";

/**
 * Colour is never the only signal: every badge carries a glyph and a word.
 *
 * The word is the bucket, not the raw outcome, so the badge and the filter
 * above it always agree. `status` is the exact one - `oracle_inconclusive`,
 * `stuck` - and it is what the large badge prints and what the small one
 * carries as its tooltip: grouped to scan, precise on inspection.
 *
 * A running run is the one case where the exact status is worse than the word:
 * it reads `incomplete`, which is what a killed run reads too. Say "Running"
 * and leave the tooltip, which is still the truth about what is on disk.
 *
 * Glyph and word are the whole badge. On black, wrapping them in a tinted
 * fill and a tinted border says the same thing a third and fourth time, and
 * the box is the part that reads loudest from across the room.
 */
export function StatusBadge({
  outcome,
  status,
  running = false,
  size = "sm",
}: {
  outcome: Outcome;
  status?: string;
  /** The launcher says this run is still going. See `bucketOf`. */
  running?: boolean;
  size?: "sm" | "lg";
}) {
  const { label, icon: Glyph, color, spin } = OUTCOME[bucketOf(outcome, running)];
  const large = size === "lg";
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium ${
        large ? "text-[13px]" : "text-[11px]"
      }`}
      style={{ color }}
      title={status}
    >
      <Glyph
        size={large ? 15 : 13}
        weight="bold"
        className={spin ? "motion-safe:animate-spin" : undefined}
        aria-hidden
      />
      {status && large && !spin ? status : label}
    </span>
  );
}
