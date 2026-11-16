"use client";

import { useLabels } from "@/lib/i18n";

export function HomeIntro() {
  const t = useLabels();
  return (
    <header className="space-y-2">
      <h1 className="text-3xl font-bold">TrueFalse</h1>
      <p className="text-slate-600 dark:text-slate-400">{t.intro}</p>
      <p className="text-sm">
        <span className="mr-2">🟢 {t.verdictTrue}</span>
        <span className="mr-2">🟠 {t.verdictMisleading}</span>
        <span className="mr-2">🔴 {t.verdictFalse}</span>
        <span>⚪ {t.verdictUnverified}</span>
      </p>
    </header>
  );
}

export function HomeTip() {
  const t = useLabels();
  return <p className="text-xs text-slate-500">{t.tip}</p>;
}

export function BackLink() {
  const t = useLabels();
  return <>{t.back}</>;
}
