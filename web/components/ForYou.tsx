"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { getForYou, type ForYouResult } from "@/lib/api";
import { savedChecks } from "@/lib/history";
import { useLabels } from "@/lib/i18n";
import { headlineClaim } from "@/lib/verdicts";

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
    getForYou(recent, navigator.language || "en")
      .then(setData)
      .catch(() => setFailed(true));
  }, []);

  useEffect(load, [load]);

  const personal = hasChecks && !!data?.topics.length;

  return (
    <div className="stack">
      <h1 className="title">{t.navForYou}</h1>
      <p className="small soft">{t.forYouNote}</p>

      {personal ? (
        <div className="row">
          <span className="small">{t.forYouBecause}:</span>
          {data.topics.map((topic) => (
            <span key={topic} className="chip">
              {topic}
            </span>
          ))}
        </div>
      ) : (
        data && (
          <div className="card row">
            <Doodle name="pudding-think" size={56} />
            <span className="min-w-0">
              <strong className="block">{t.forYouLatest}</strong>
              {!hasChecks && <span className="small soft">{t.forYouEmpty}</span>}
            </span>
          </div>
        )
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
        <ul className="foryou">
          {data.items.map((item) => (
            <li key={item.url} className="card">
              <span className="small soft">
                {item.publisher}
                {item.published ? ` · ${new Date(item.published * 1000).toLocaleDateString()}` : ""}
              </span>
              <strong className="block wrap-any" dir="auto">
                {item.title}
              </strong>
              {item.why && (
                <span className="small block why" dir="auto">
                  <Doodle name="heart" size={18} /> {item.why}
                </span>
              )}
              <div className="row mt-3">
                <a className="btn" href={item.url} target="_blank" rel="noopener noreferrer">
                  {t.forYouRead}
                </a>
                <Link className="btn btn-main" href={`/?text=${encodeURIComponent(item.title)}`}>
                  {t.forYouCheck}
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
