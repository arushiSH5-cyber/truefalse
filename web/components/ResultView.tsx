"use client";

import { useEffect, useState } from "react";
import { cardUrl, getJob, type JobRecord, type Verdict } from "@/lib/api";

const STYLE: Record<Verdict, { emoji: string; band: string }> = {
  true: { emoji: "🟢", band: "bg-verdict-true" },
  misleading: { emoji: "🟠", band: "bg-verdict-misleading" },
  false: { emoji: "🔴", band: "bg-verdict-false" },
  unverified: { emoji: "⚪", band: "bg-verdict-unverified" },
};
const POLL_MS = 2000;
const GIVE_UP_MS = 6 * 60 * 1000;

export default function ResultView({ id }: { id: string }) {
  const [job, setJob] = useState<JobRecord | null>(null);
  const [missing, setMissing] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const [timedOut, setTimedOut] = useState(false);

  useEffect(() => {
    let stop = false;
    const started = Date.now();
    async function poll() {
      while (!stop) {
        try {
          const rec = await getJob(id);
          if (!rec) {
            setMissing(true);
            return;
          }
          setJob(rec);
          if (rec.status === "done" || rec.status === "failed") return;
        } catch {
          /* transient network error on a weak connection: keep polling */
        }
        if (Date.now() - started > GIVE_UP_MS) {
          setTimedOut(true);
          return;
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => {
      stop = true;
    };
  }, [id]);

  if (missing) return <p>This result has expired or does not exist.</p>;
  if (!job || job.status === "queued" || job.status === "working") {
    return (
      <div aria-live="polite" className="space-y-3">
        <p className="text-lg font-medium">🔎 Checking…</p>
        {job?.messages.slice(-1).map((m) => (
          <p key={m} className="text-slate-600 dark:text-slate-400" dir="auto">
            {m}
          </p>
        ))}
        {timedOut && <p>This is taking longer than usual. Please refresh in a minute.</p>}
      </div>
    );
  }
  if (job.status === "failed" || !job.result) {
    return <p>Sorry, something went wrong while checking this. Please try again.</p>;
  }

  const r = job.result;
  const s = STYLE[r.verdict];

  async function share() {
    try {
      const blob = await (await fetch(cardUrl(id))).blob();
      const file = new File([blob], "truefalse.png", { type: "image/png" });
      if (navigator.canShare?.({ files: [file] })) {
        await navigator.share({ files: [file], text: `${s.emoji} ${r.verdict_label}` });
        return;
      }
    } catch {
      /* fall back to opening the image */
    }
    window.open(cardUrl(id), "_blank", "noopener");
  }

  function listen() {
    const u = new SpeechSynthesisUtterance(`${r.verdict_label}. ${r.explanation}`);
    u.lang = r.language;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }

  return (
    <article className="overflow-hidden rounded-2xl bg-white shadow dark:bg-slate-900" lang={r.language}>
      <div className={`${s.band} px-5 py-4 text-white`}>
        <p className="text-2xl font-bold" dir="auto">
          {s.emoji} {r.verdict_label}
        </p>
      </div>
      <div className="space-y-4 p-5">
        {r.claims[0] && (
          <p className="text-sm italic text-slate-500" dir="auto">
            “{r.claims[0]}”
          </p>
        )}
        <p className="text-lg leading-relaxed" dir="auto">
          {r.explanation}
        </p>
        <p className="text-sm font-medium">Confidence: {r.confidence}</p>
        {r.warnings.length > 0 && (
          <ul className="space-y-1 text-sm text-amber-800 dark:text-amber-300" dir="auto">
            {r.warnings.map((w) => (
              <li key={w}>⚠️ {w}</li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2">
          <button onClick={share} className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-white dark:bg-white dark:text-slate-900">
            Share correction card
          </button>
          <button onClick={listen} className="rounded-lg border px-3 py-2 text-sm">
            🔊 Listen
          </button>
          <button onClick={() => setShowMore((v) => !v)} className="rounded-lg border px-3 py-2 text-sm">
            {showMore ? "Less" : "More: sources & full answer"}
          </button>
        </div>
        {showMore && (
          <div className="space-y-3 border-t pt-3">
            {r.sources.length > 0 ? (
              <ol className="list-decimal space-y-2 pl-5 text-sm">
                {r.sources.map((src) => (
                  <li key={src.url}>
                    <a href={src.url} target="_blank" rel="noopener noreferrer" className="underline">
                      {src.title}
                    </a>
                    {src.publisher && <span className="text-slate-500"> · {src.publisher}</span>}
                    {src.published && <span className="text-slate-500"> · {src.published}</span>}
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm">No trustworthy source was found for this.</p>
            )}
            <pre className="whitespace-pre-wrap text-xs text-slate-500" dir="auto">
              {r.reply_text}
            </pre>
          </div>
        )}
      </div>
    </article>
  );
}
