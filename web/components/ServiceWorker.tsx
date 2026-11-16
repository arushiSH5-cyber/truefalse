"use client";

import { useEffect } from "react";
import { clientId } from "@/lib/api";
import { DEMO } from "@/lib/demo";

export default function ServiceWorker() {
  useEffect(() => {
    if (DEMO) return; // static demo: no share target, no offline shell
    clientId(); // make sure the share-target cookie exists
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  return null;
}
