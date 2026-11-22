"use client";

import { useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { useLabels } from "@/lib/i18n";
import { install, watchInstall, type InstallState } from "@/lib/install";

/** One quiet row on the home screen; gone once the app is installed. */
export default function InstallCard() {
  const t = useLabels();
  const [app, setApp] = useState<InstallState>("installed");

  useEffect(() => watchInstall(setApp), []);

  if (app === "installed") return null;
  return app === "ready" ? (
    <button type="button" className="btn" onClick={install}>
      <Doodle name="phone" size={28} />
      {t.installButton}
    </button>
  ) : (
    <details className="small">
      <summary className="cursor-pointer py-2 font-bold">{t.installTitle}</summary>
      <p>{t.installHow}</p>
    </details>
  );
}
