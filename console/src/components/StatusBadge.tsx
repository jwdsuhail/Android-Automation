import type { Outcome } from "../api";
import { OUTCOME } from "../format";

/**
 * Colour is never the only signal: every badge carries a glyph and a word.
 * Seven of seventeen finished runs here are `oracle_inconclusive`, and a
 * reader who cannot tell those from a failed task cannot read the board.
 *
 * Glyph and word are the whole badge. On black, wrapping them in a tinted
 * fill and a tinted border says the same thing a third and fourth time, and
 * the box is the part that reads loudest from across the room.
 */
export function StatusBadge({
  outcome,
  status,
  size = "sm",
}: {
  outcome: Outcome;
  status?: string;
  size?: "sm" | "lg";
}) {
  const { label, icon: Glyph, color } = OUTCOME[outcome];
  const large = size === "lg";
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium ${
        large ? "text-[13px]" : "text-[11px]"
      }`}
      style={{ color }}
      title={status}
    >
      <Glyph size={large ? 15 : 13} weight="bold" aria-hidden />
      {status && large ? status : label}
    </span>
  );
}
