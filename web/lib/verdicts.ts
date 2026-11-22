import type { PublicResult, Verdict } from "@/lib/api";
import type { Labels } from "@/lib/i18n";

/** One buddy, badge and sticker pattern per verdict. Color is never the only signal. */
export const VERDICTS: Record<
  Verdict,
  { buddy: string; badge: string; pattern: string; label: keyof Labels; line: keyof Labels }
> = {
  true: { buddy: "mochi-happy", badge: "check", pattern: "pat-dots", label: "verdictTrue", line: "lineTrue" },
  misleading: {
    buddy: "pudding-think",
    badge: "wavy",
    pattern: "pat-check",
    label: "verdictMisleading",
    line: "lineMisleading",
  },
  false: { buddy: "berry-wow", badge: "cross", pattern: "pat-gingham", label: "verdictFalse", line: "lineFalse" },
  unverified: {
    buddy: "luna-sleep",
    badge: "question",
    pattern: "pat-stripe",
    label: "verdictUnverified",
    line: "lineUnverified",
  },
};

/** The claim the overall verdict is about. Never show claims[0] under it: that one may be true. */
export function headlineClaim(r: PublicResult): string {
  const match = r.claim_results?.find((c) => c.verdict === r.verdict);
  if (match) return match.claim;
  return r.claims.length === 1 ? r.claims[0] : r.explanation;
}
