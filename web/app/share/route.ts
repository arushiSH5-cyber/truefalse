/**
 * Web Share Target endpoint. Android/ChromeOS "Share" from any app (WhatsApp, Instagram,
 * YouTube, TikTok…) POSTs multipart form data here (see manifest.webmanifest).
 * We forward it to the backend and redirect to the result page.
 */
import { NextResponse, type NextRequest } from "next/server";

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
    method: "POST",
    body: out,
    headers: {
      "X-Client-Id": clientId,
      "Accept-Language": req.headers.get("accept-language") ?? "",
    },
  });
  const base = new URL("/", req.url);
  if (!res.ok) {
    base.searchParams.set("error", String(res.status));
    return NextResponse.redirect(base, 303);
  }
  const { id } = (await res.json()) as { id: string };
  const response = NextResponse.redirect(new URL(`/result/${id}`, req.url), 303);
  response.cookies.set("tf_cid", clientId, { path: "/", maxAge: 31536000, sameSite: "lax" });
  return response;
}
