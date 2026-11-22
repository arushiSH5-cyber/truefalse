"use client";

import Doodle from "@/components/Doodle";
import { useLabels } from "@/lib/i18n";
import { VERDICTS } from "@/lib/verdicts";

export function HomeIntro() {
  const t = useLabels();
  return (
    <section className="hero">
      <div>
        <h1 className="title">{t.headline}</h1>
        <p className="hand soft mt-2">{t.subline}</p>
      </div>
      <Doodle name="scout-think" size={170} />
    </section>
  );
}

export function Legend() {
  const t = useLabels();
  return (
    <ul className="legend">
      {Object.entries(VERDICTS).map(([verdict, v]) => (
        <li key={verdict}>
          <span className={`sticker ${v.pattern}`}>
            <Doodle name={v.buddy} size={80} />
          </span>
          {t[v.label]}
        </li>
      ))}
    </ul>
  );
}

export function HomeTip() {
  const t = useLabels();
  return (
    <details className="small soft">
      <summary className="cursor-pointer py-2 font-bold">{t.privacyTitle}</summary>
      <p dir="auto">{t.privacy}</p>
      <p dir="auto">{t.freeTier}</p>
    </details>
  );
}

export function BackLink() {
  const t = useLabels();
  return <>{t.back}</>;
}
