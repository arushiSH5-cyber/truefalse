"use client";

import { useEffect } from "react";
import { clientId } from "@/lib/api";

export default function ServiceWorker() {
  useEffect(() => {
    clientId(); // make sure the share-target cookie exists
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
  }, []);
  return null;
}
