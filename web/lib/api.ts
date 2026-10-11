export type Verdict = "true" | "misleading" | "false" | "unverified";

export interface PublicSource {
  title: string;
  url: string;
  publisher: string | null;
  tier: number;
  published: string | null;
}

export interface PublicResult {
  verdict: Verdict;
  verdict_label: string;
  confidence: "high" | "medium" | "low";
  language: string;
  reply_text: string;
  explanation: string;
  warnings: string[];
  sources: PublicSource[];
  claims: string[];
}

export interface JobRecord {
  id: string;
  status: "queued" | "working" | "done" | "failed";
  messages: string[];
  result: PublicResult | null;
}

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const CLIENT_COOKIE = "tf_cid";

/** Random anonymous id (no personal data). Mirrored into a cookie for the share target. */
export function clientId(): string {
  const key = "truefalse.clientId";
  let id: string | null = null;
  try {
    id = localStorage.getItem(key);
    if (!id) {
      id = crypto.randomUUID().replace(/-/g, "");
      localStorage.setItem(key, id);
    }
  } catch {
    id = crypto.randomUUID().replace(/-/g, "");
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
  const res = await fetch(`${API_URL}/api/check/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Error ${res.status}`);
  return (await res.json()) as JobRecord;
}

export function cardUrl(id: string): string {
  return `${API_URL}/api/check/${encodeURIComponent(id)}/card.png`;
}
