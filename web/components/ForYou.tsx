"use client";

import { useCallback, useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { getForYou, type ForYouItem, type ForYouResult } from "@/lib/api";
import { savedChecks } from "@/lib/history";
import { type Labels, useLabels } from "@/lib/i18n";
import { LANG_EVENT, uiLanguage } from "@/lib/lang";
import { headlineClaim } from "@/lib/verdicts";

function badge(t: Labels, verdict: ForYouItem["verdict"]): string {
  return {
    false: t.verdictFalse,
    misleading: t.verdictMisleading,
    true: t.verdictTrue,
    mixed: t.verdictMixed,
    "": "",
  }[verdict];
}

function MythCard({ item, t }: { item: ForYouItem; t: Labels }) {
  const label = badge(t, item.verdict);
  return (
    <li className="card myth">
      <span className="small soft">
        {item.publisher}
        {item.published ? ` · ${new Date(item.published * 1000).toLocaleDateString()}` : ""}
      </span>
      {label && <span className={`vstamp vstamp-${item.verdict}`}>{label}</span>}
      {item.myth ? (
        <p className="wrap-any" dir="auto">
          <span className="small soft block">{t.forYouMyth}</span>
          <strong className={item.verdict === "false" ? "struck" : ""}>{item.myth}</strong>
        </p>
      ) : (
        <strong className="block wrap-any" dir="auto">
          {item.title}
        </strong>
      )}
      {item.truth && (
        <p className="wrap-any" dir="auto">
          <span className="small soft block">{t.forYouTruth}</span>
          {item.truth}
        </p>
      )}
      <div className="row">
        <a className="btn" href={item.url} target="_blank" rel="noopener noreferrer">
          {t.forYouRead}
        </a>
        {item.why && (
          <span className="small why" dir="auto">
            <Doodle name="heart" size={18} /> {item.why}
          </span>
        )}
      </div>
    </li>
  );
}

export default function ForYou() {
  const t = useLabels();
  const [data, setData] = useState<ForYouResult | null>(null);
  const [failed, setFailed] = useState(false);
  const [hasChecks, setHasChecks] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    setData(null);
    const recent = savedChecks().map((c) => headlineClaim(c.result)).filter(Boolean);
    setHasChecks(recent.length > 0);
    getForYou(recent, uiLanguage())
      .then(setData)
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener(LANG_EVENT, load);
    return () => window.removeEventListener(LANG_EVENT, load);
  }, [load]);

  const personal = hasChecks && !!data?.topics.length;

  return (
    <div className="stack">
      <h1 className="title">{t.navForYou}</h1>
      <p className="small soft">{t.forYouNote}</p>

      {personal && (
        <div className="row">
          <span className="small">{t.forYouBecause}:</span>
          {data.topics.map((topic) => (
            <span key={topic} className="chip">
              {topic}
            </span>
          ))}
        </div>
      )}
      {data && !hasChecks && (
        <div className="card row">
          <Doodle name="pudding-think" size={56} />
          <span className="small soft min-w-0">{t.forYouEmpty}</span>
        </div>
      )}

      {!data && !failed && (
        <div className="card waiting">
          <Doodle name="scout-think" size={120} />
          <p className="hand text-xl">{t.checking}</p>
        </div>
      )}

      {failed && (
        <div className="card waiting">
          <Doodle name="scout-sad" size={120} />
          <p>{t.forYouFailed}</p>
          <button type="button" className="btn btn-main mt-3" onClick={load}>
            {t.forYouAgain}
          </button>
        </div>
      )}

      {!!data?.items.length && (
        <>
          <h2 className="hand text-xl">{t.forYouLatest}</h2>
          <ul className="foryou">
            {data.items.map((item) => (
              <MythCard key={item.url} item={item} t={t} />
            ))}
          </ul>
        </>
      )}

      {!!data?.myths?.length && (
        <>
          <h2 className="hand text-xl">{t.forYouMyths}</h2>
          <ul className="foryou">
            {data.myths.map((item) => (
              <MythCard key={item.myth} item={item} t={t} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
