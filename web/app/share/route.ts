/**
 * Web Share Target endpoint. Android/ChromeOS "Share" from any app (WhatsApp, Instagram,
 * YouTube, TikTok…) POSTs multipart form data here (see manifest.webmanifest).
 * We forward it to the backend and redirect to the result page.
 */
import { NextResponse, type NextRequest } from "next/server";

// Relative Location: behind a proxy or on a phone, req.url's host is not the one the user typed.
const seeOther = (location: string) => new NextResponse(null, { status: 303, headers: { location } });

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const incoming = await req.formData();
  const text = ["title", "text", "url"]
    .map((k) => incoming.get(k))
    .filter((v): v is string => typeof v === "string" && v.trim() !== "")
    .join("\n");
  const out = new FormData();
  out.set("text", text);
  const file = incoming.get("file");
  if (file instanceof File && file.size > 0) out.set("file", file);

  const clientId = req.cookies.get("tf_cid")?.value ?? crypto.randomUUID().replace(/-/g, "");
  const res = await fetch(`${API_URL}/api/check`, {
    signal: AbortSignal.timeout(30_000),
    method: "POST",
    body: out,
    headers: {
      "X-Client-Id": clientId,
      "Accept-Language": req.headers.get("accept-language") ?? "",
    },
  }).catch(() => null);
  if (!res?.ok) return seeOther(`/?error=${res?.status ?? "offline"}`);
  const { id } = (await res.json()) as { id: string };
  const response = seeOther(`/result/${id}`);
  response.cookies.set("tf_cid", clientId, { path: "/", maxAge: 31536000, sameSite: "lax" });
  return response;
}
