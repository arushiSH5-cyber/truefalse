"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { STATIC } from "@/lib/demo";
import { forgetAll, forgetCheck, savedChecks, type SavedCheck } from "@/lib/history";
import { useLabels } from "@/lib/i18n";
import { headlineClaim, VERDICTS } from "@/lib/verdicts";

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export default function History() {
  const t = useLabels();
  const [list, setList] = useState<SavedCheck[] | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => setList(savedChecks()), []);

  return (
    <div className="stack">
      <h1 className="title">{t.navHistory}</h1>
      <p className="small soft">{t.historyNote}</p>
      {list?.length === 0 && (
        <div className="card waiting">
          <Doodle name="pudding-sleep" size={156} />
          <p className="hand text-xl">{t.historyEmpty}</p>
          <Link href="/" className="btn btn-main mt-3">
            {t.checkIt}
          </Link>
        </div>
      )}
      {!!list?.length && (
        <>
          <ul className="history">
            {list.map(({ id, at, result }) => (
              <li key={id} className={VERDICTS[result.verdict].pattern}>
                {/* A plain link to one precached page, so saved receipts also open offline. */}
                <a href={`${BASE}/result/saved${STATIC ? "/" : ""}?id=${id}`}>
                  <Doodle name={VERDICTS[result.verdict].buddy} size={64} />
                  <span className="min-w-0">
                    <strong className="block">{result.verdict_label}</strong>
                    <span className="clamp small wrap-any" dir="auto">
                      {headlineClaim(result)}
                    </span>
                    <span className="small block">{new Date(at).toLocaleDateString()}</span>
                  </span>
                </a>
                <button type="button" className="round" aria-label={t.remove} onClick={() => setList(forgetCheck(id))}>
                  <Doodle name="bin" size={28} />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="btn"
            onBlur={() => setConfirming(false)}
            onClick={() => {
              if (!confirming) return setConfirming(true);
              forgetAll();
              setList([]);
            }}
          >
            {confirming ? t.clearSure : t.clearAll}
          </button>
        </>
      )}
    </div>
  );
}
