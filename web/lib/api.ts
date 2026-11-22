import { DEMO, demoJob } from "@/lib/demo";

export type Verdict = "true" | "misleading" | "false" | "unverified";

export interface PublicSource {
  title: string;
  url: string;
  publisher: string | null;
  tier: number;
  published: string | null;
}

export type AIOverall = "likely_ai" | "possibly_ai" | "recycled" | "edited" | "no_signal";

export interface AISignal {
  detector: string;
  media: "image" | "video" | "audio" | "text";
  finding: "ai_generated" | "edited" | "recycled" | "camera_original" | "no_finding";
  strength: "strong" | "moderate" | "weak";
  score: number | null;
  detail: string;
  source_url: string | null;
}

export interface PublicClaim {
  claim: string;
  verdict: Verdict;
}

export interface PublicResult {
  verdict: Verdict;
  verdict_label: string;
  confidence: "high" | "medium" | "low";
  language: string;
  reply_text: string;
  /** Two-line reason, in the reply language. */
  explanation: string;
  /** Short message to forward to the chat the claim came from; ends with the top source link. */
  correction: string;
  confidence_label: string;
  sources_heading: string;
  forward_heading: string;
  warnings: string[];
  sources: PublicSource[];
  claims: string[];
  /** Each claim's own verdict, same order as `claims`. Missing on results from older servers. */
  claim_results?: PublicClaim[];
  needs_human_review: boolean;
  ai_overall: AIOverall;
  /** Every AI-content check result, weak hints included (shown under "More" only). */
  ai_signals: AISignal[];
}

export interface JobRecord {
  id: string;
  status: "queued" | "working" | "done" | "failed";
  messages: string[];
  result: PublicResult | null;
}

// Same origin by default (next.config rewrites /backend to the API), so a phone on your Wi-Fi works.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/backend";
export const CLIENT_COOKIE = "tf_cid";

/** crypto.randomUUID only exists on HTTPS and localhost; a phone testing over Wi-Fi has neither. */
function randomId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Random anonymous id (no personal data). Mirrored into a cookie for the share target. */
export function clientId(): string {
  const key = "truefalse.clientId";
  let id: string | null = null;
  try {
    id = localStorage.getItem(key);
    if (!id) {
      id = randomId();
      localStorage.setItem(key, id);
    }
  } catch {
    id = randomId();
  }
  document.cookie = `${CLIENT_COOKIE}=${id}; path=/; max-age=31536000; samesite=lax`;
  return id;
}

export async function submitCheck(form: FormData): Promise<string> {
  const res = await fetch(`${API_URL}/api/check`, {
    method: "POST",
    body: form,
    headers: { "X-Client-Id": clientId() },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(typeof detail.detail === "string" ? detail.detail : `Error ${res.status}`);
  }
  return (await res.json()).id as string;
}

export async function getJob(id: string): Promise<JobRecord | null> {
  if (DEMO) return demoJob(id);
  const res = await fetch(`${API_URL}/api/check/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Error ${res.status}`);
  return (await res.json()) as JobRecord;
}

/** Local mode reports whether it has an AI key. Other deployments don't have this route. */
export async function getStatus(): Promise<"ok" | "no-key" | "down"> {
  try {
    const res = await fetch(`${API_URL}/api/status`, { cache: "no-store" });
    if (res.status === 404) return "ok";
    if (!res.ok) return "down";
    return (await res.json()).ready ? "ok" : "no-key";
  } catch {
    return "down";
  }
}

export function cardUrl(id: string): string {
  return `${API_URL}/api/check/${encodeURIComponent(id)}/card.png`;
}

export async function sendFeedback(id: string, rating: "up" | "down", note = ""): Promise<void> {
  if (DEMO) return;
  const res = await fetch(`${API_URL}/api/check/${encodeURIComponent(id)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating, note }),
    headers: { "Content-Type": "application/json", "X-Client-Id": clientId() },
  });
  if (!res.ok) throw new Error(`Error ${res.status}`);
}
