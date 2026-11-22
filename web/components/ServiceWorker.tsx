"use client";

import { useEffect } from "react";
import { clientId } from "@/lib/api";
import { DEMO } from "@/lib/demo";
import { captureInstall } from "@/lib/install";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export default function ServiceWorker() {
  useEffect(() => {
    if (!DEMO) clientId(); // the share-target cookie; the demo has no server
    const release = captureInstall();
    // Only on built sites: caching would serve stale files in `next dev`.
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator) {
      navigator.serviceWorker.register(`${BASE}/sw.js`, { scope: `${BASE}/` }).catch(() => undefined);
    }
    return release;
  }, []);
  return null;
}
