"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import Doodle from "@/components/Doodle";
import { getForYou, type ForYouItem, type ForYouResult } from "@/lib/api";
import { savedChecks } from "@/lib/history";
import { type Labels, useLabels } from "@/lib/i18n";
import { LANG_EVENT, uiLanguage } from "@/lib/lang";
import { BAKED_MYTHS } from "@/lib/myths";
import { headlineClaim } from "@/lib/verdicts";

type Reel = ForYouItem & { key: string; fresh: boolean };

const LOOK: Record<ForYouItem["verdict"], { buddy: string; pattern: string }> = {
  false: { buddy: "berry-wow", pattern: "pat-gingham" },
  misleading: { buddy: "pudding-think", pattern: "pat-check" },
  mixed: { buddy: "pudding-think", pattern: "pat-check" },
  true: { buddy: "mochi-happy", pattern: "pat-dots" },
  "": { buddy: "luna-sleep", pattern: "pat-stripe" },
};

function badge(t: Labels, verdict: ForYouItem["verdict"]): string {
  return {
    false: t.verdictFalse,
    misleading: t.verdictMisleading,
    true: t.verdictTrue,
    mixed: t.verdictMixed,
    "": "",
  }[verdict];
}

/** Shown at once, before (or without) the checker answering. */
const BAKED: Reel[] = BAKED_MYTHS.map((m) => ({
  key: `baked:${m.myth}`,
  fresh: false,
  title: m.myth,
  url: m.url,
  publisher: m.publisher,
  language: "en",
  summary: m.truth,
  published: null,
  why: m.topic,
  myth: m.myth,
  verdict: "false",
  truth: m.truth,
}));

function toReels(data: ForYouResult | null): Reel[] {
  if (!data) return BAKED;
  const fresh: Reel[] = data.items.map((i) => ({ ...i, key: `live:${i.url}`, fresh: true }));
  const myths: Reel[] = (data.myths ?? []).map((i) => ({ ...i, key: `myth:${i.myth}`, fresh: false }));
  // Live fact-checks first, then the common myths (the server's copy is ranked and translated).
  const seen = new Set<string>();
  return [...fresh, ...(myths.length ? myths : BAKED)].filter((r) => {
    const id = (r.myth || r.title).toLowerCase();
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function ReelCard({ reel, n, total, t }: { reel: Reel; n: number; total: number; t: Labels }) {
  const look = LOOK[reel.verdict] ?? LOOK[""];
  const label = badge(t, reel.verdict);
  const myth = reel.myth || reel.title;
  // A stated myth gets struck out; a headline (the fact-checker's own words) does not.
  const stated = !!reel.myth && reel.myth.trim() !== reel.title.trim();
  return (
    <li className="reel">
      <div className="reel-top small">
        <span className="chip">{reel.fresh ? t.forYouLatest : reel.why || t.forYouMyths}</span>
        <span className="soft">
          {n} / {total}
        </span>
      </div>

      <div className="reel-body">
        <span className={`sticker reel-buddy ${look.pattern}`}>
          <Doodle name={look.buddy} size={96} />
        </span>
        {label && <span className={`vstamp vstamp-${reel.verdict}`}>{label}</span>}
        <p className="wrap-any" dir="auto">
          {stated && <span className="small soft block">{t.forYouMyth}</span>}
          <strong className={`reel-myth ${stated && reel.verdict === "false" ? "struck" : ""}`}>{myth}</strong>
        </p>
        {reel.truth && (
          <p className="wrap-any reel-truth" dir="auto" title={reel.truth}>
            <span className="small soft block">{t.forYouTruth}</span>
            {reel.truth}
          </p>
        )}
      </div>

      <div className="reel-foot">
        <span className="small soft">
          {reel.publisher}
          {reel.published ? ` · ${new Date(reel.published * 1000).toLocaleDateString()}` : ""}
        </span>
        <div className="row">
          <a className="btn" href={reel.url} target="_blank" rel="noopener noreferrer">
            {t.forYouRead}
          </a>
          <Link className="btn btn-main" href={`/?text=${encodeURIComponent(myth)}`}>
            {t.forYouCheck}
          </Link>
        </div>
      </div>
    </li>
  );
}

export default function ForYou() {
  const t = useLabels();
  const [data, setData] = useState<ForYouResult | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    const recent = savedChecks().map((c) => headlineClaim(c.result)).filter(Boolean);
    getForYou(recent, uiLanguage())
      .then(setData)
      .catch(() => {}) // keep the baked-in myths; the page is never empty
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(LANG_EVENT, load);
    return () => window.removeEventListener(LANG_EVENT, load);
  }, [load]);

  const reels = useMemo(() => toReels(data), [data]);

  return (
    <div className="reel-page">
      <div className="reel-head">
        <h1 className="title">{t.navForYou}</h1>
        {loading && (
          <span className="small soft row">
            <Doodle name="heart" size={18} /> {t.checking}
          </span>
        )}
      </div>
      <ul className="reels" aria-label={t.navForYou}>
        {reels.map((reel, i) => (
          <ReelCard key={reel.key} reel={reel} n={i + 1} total={reels.length} t={t} />
        ))}
      </ul>
    </div>
  );
}
