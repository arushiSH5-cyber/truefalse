// Talks to the TrueFalse web API (backend/app/api/web.py). Shared by every extension page.

export const DEFAULT_API = "http://localhost:8000";

export async function apiUrl() {
  const { apiUrl } = await chrome.storage.sync.get({ apiUrl: DEFAULT_API });
  return apiUrl.replace(/\/+$/, "");
}

/** Random anonymous id (no personal data), used only for rate limiting. */
export async function clientId() {
  const { clientId } = await chrome.storage.local.get("clientId");
  if (clientId) return clientId;
  const id = crypto.randomUUID().replace(/-/g, "");
  await chrome.storage.local.set({ clientId: id });
  return id;
}

export function isWebUrl(url) {
  return /^https?:\/\//i.test(url ?? "");
}

/** A failed fetch is a TypeError; only then is "is the server running?" useful advice. */
export function explainError(err) {
  if (err instanceof TypeError) {
    return "Could not reach TrueFalse. Is the server running? Check its address in Settings.";
  }
  return err instanceof Error ? err.message : String(err);
}

async function errorText(res) {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `Error ${res.status}`;
}

/** Start a check. `input` has any of: text, url, file (Blob) + filename. Returns the job id. */
export async function submitCheck(input) {
  const form = new FormData();
  if (input.text) form.set("text", input.text.slice(0, 10000));
  if (input.url) form.set("url", input.url.slice(0, 2000));
  if (input.file) form.set("file", input.file, input.filename || "image");
  const res = await fetch(`${await apiUrl()}/api/check`, {
    method: "POST",
    body: form,
    headers: { "X-Client-Id": await clientId(), "Accept-Language": navigator.language },
  });
  if (!res.ok) throw new Error(await errorText(res));
  return (await res.json()).id;
}

export async function getJob(id) {
  const res = await fetch(`${await apiUrl()}/api/check/${encodeURIComponent(id)}`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(await errorText(res));
  return res.json();
}

export async function sendFeedback(id, rating, note = "") {
  const res = await fetch(`${await apiUrl()}/api/check/${encodeURIComponent(id)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ rating, note }),
    headers: { "Content-Type": "application/json", "X-Client-Id": await clientId() },
  });
  if (!res.ok) throw new Error(await errorText(res));
}

export function openResult(id) {
  return chrome.windows.create({
    url: chrome.runtime.getURL(`result.html?id=${encodeURIComponent(id)}`),
    type: "popup",
    width: 440,
    height: 680,
  });
}
