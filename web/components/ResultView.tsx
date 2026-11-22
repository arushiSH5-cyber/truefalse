"use client";

import { useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { cardUrl, getJob, sendFeedback, type AISignal, type JobRecord, type PublicResult } from "@/lib/api";
import { copyText, isWebUrl } from "@/lib/browser";
import { DEMO } from "@/lib/demo";
import { saveCheck, savedCheck } from "@/lib/history";
import { labelsFor, useLabels, type Labels } from "@/lib/i18n";
import { VERDICTS } from "@/lib/verdicts";

const STRENGTH: Record<AISignal["strength"], string> = {
  strong: "strong",
  moderate: "medium",
  weak: "weak hint",
};
const POLL_MS = 2000;
const GIVE_UP_MS = 6 * 60 * 1000;

function State({ buddy, title, children }: { buddy: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="card waiting" aria-live="polite">
      <Doodle name={buddy} size={156} className={buddy === "scout-think" ? "bob" : undefined} />
      <p className="display mt-3 text-2xl">{title}</p>
      {children}
    </div>
  );
}

export default function ResultView({ id: param }: { id: string }) {
  // /result/saved?id=… is the one result page the service worker can keep for offline.
  const [id, setId] = useState(param === "saved" ? "" : param);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [missing, setMissing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const t = useLabels();

  useEffect(() => {
    if (param !== "saved") return;
    const wanted = new URLSearchParams(location.search).get("id");
    if (wanted) setId(wanted);
    else setMissing(true);
  }, [param]);

  useEffect(() => {
    if (!id) return;
    const kept = savedCheck(id);
    if (kept) {
      setJob({ id, status: "done", messages: [], result: kept.result });
      return;
    }
    let stop = false;
    const started = Date.now();
    const clock = setInterval(() => setSeconds(Math.round((Date.now() - started) / 1000)), 1000);
    async function poll() {
      while (!stop) {
        try {
          const rec = await getJob(id);
          if (stop) return;
          setOffline(false);
          if (!rec) return setMissing(true);
          setJob(rec);
          if (rec.status === "done" && rec.result) saveCheck(id, rec.result);
          if (rec.status === "done" || rec.status === "failed") return clearInterval(clock);
        } catch {
          setOffline(true); // weak connection: keep trying
        }
        if (Date.now() - started > GIVE_UP_MS) return setTimedOut(true);
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => {
      stop = true;
      clearInterval(clock);
    };
  }, [id]);

  if (missing) return <State buddy="luna-sleep" title={t.expired} />;
  if (!job || job.status === "queued" || job.status === "working") {
    return (
      <State buddy={offline ? "scout-sad" : "scout-think"} title={offline ? t.serverDown : t.checking}>
        {job?.messages.slice(-1).map((m) => (
          <p key={m} className="soft wrap-any" dir="auto">
            {m}
          </p>
        ))}
        <p className="hand soft" aria-hidden="true">
          {seconds}s
        </p>
        {timedOut && <p>{t.slow}</p>}
      </State>
    );
  }
  if (job.status === "failed" || !job.result) {
    return (
      <State buddy="scout-sad" title={t.failed}>
        <p className="small soft">{t.failedHint}</p>
      </State>
    );
  }
  return <Receipt id={id} result={job.result} t={t} />;
}

function Receipt({ id, result: r, t }: { id: string; result: PublicResult; t: Labels }) {
  const v = VERDICTS[r.verdict];
  const answer = labelsFor(r.language); // claim verdicts read in the answer's language

  return (
    <article className="receipt" lang={r.language}>
      <header className={v.pattern}>
        <Doodle name={v.buddy} size={130} />
        <div className="min-w-0">
          <h1 className="stamp" dir="auto">
            {r.verdict_label}
          </h1>
          <p className="hand mt-2" dir="auto">
            {answer[v.line]}
          </p>
        </div>
      </header>

      <section className="receipt-part">
        <p className="wrap-any text-lg" dir="auto">
          {r.explanation}
        </p>
        <p className="tag mt-2" dir="auto">
          {r.confidence_label || `Confidence: ${r.confidence}`}
        </p>
      </section>

      {r.claims.length > 0 && (
        <section className="receipt-part">
          <h2 className="receipt-label">{t.weChecked}</h2>
          <ul>
            {r.claims.map((claim, i) => {
              // One claim and an old record without claim_results: the overall verdict is its verdict.
              const verdict = r.claim_results?.[i]?.verdict ?? (r.claims.length === 1 ? r.verdict : null);
              const item = verdict && VERDICTS[verdict];
              return (
                <li key={i} className="line-item">
                  <Doodle name={item ? item.badge : "note"} size={38} />
                  <div>
                    {item && (
                      <strong className="block" dir="auto">
                        {answer[item.label]}
                      </strong>
                    )}
                    <span className="soft wrap-any block" dir="auto">
                      “{claim}”
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {(r.needs_human_review || r.warnings.length > 0) && (
        <ul className="receipt-part small">
          {r.needs_human_review && (
            <li className="line-item">
              <Doodle name="scout-think" size={34} />
              <div>{t.review}</div>
            </li>
          )}
          {r.warnings.map((w) => (
            <li key={w} className="line-item">
              <Doodle name="warning" size={30} />
              <div className="wrap-any" dir="auto">
                {w}
              </div>
            </li>
          ))}
        </ul>
      )}

      <section className="receipt-part">
        <h2 className="receipt-label" dir="auto">
          {r.sources_heading}
        </h2>
        {r.sources.length > 0 ? (
          <ol>
            {r.sources.map((src, i) => (
              <li key={`${src.url}-${i}`} className="line-item source">
                <Doodle name={src.tier === 1 ? "shield" : "link"} size={30} />
                <div>
                  {isWebUrl(src.url) ? (
                    <a href={src.url} target="_blank" rel="noopener noreferrer" dir="auto">
                      {src.title}
                    </a>
                  ) : (
                    <span className="wrap-any">{src.title}</span>
                  )}
                  <p className="small soft wrap-any">
                    {[src.publisher, src.published].filter(Boolean).join(" · ")}{" "}
                    <span className="tag">{[t.tier1, t.tier2, t.tier3][src.tier - 1] ?? t.tier3}</span>
                  </p>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="small">{t.noSource}</p>
        )}
      </section>

      {r.ai_signals.length > 0 && (
        <details className="receipt-part small">
          <summary className="receipt-label cursor-pointer">{t.aiChecks}</summary>
          <ul>
            {r.ai_signals.map((sig, i) => (
              <li key={`${sig.detector}-${i}`} className="wrap-any py-1">
                {sig.detail || `${sig.detector}: ${sig.finding}`} ({STRENGTH[sig.strength]}){" "}
                {sig.source_url && isWebUrl(sig.source_url) && (
                  <a href={sig.source_url} target="_blank" rel="noopener noreferrer" className="underline">
                    link
                  </a>
                )}
              </li>
            ))}
          </ul>
          <p className="soft">{t.weakNote}</p>
        </details>
      )}

      {r.correction && <SendBack result={r} t={t} />}
      <Extras id={id} result={r} t={t} />
      <Feedback id={id} t={t} />
      <p className="hand soft pt-2 text-center">{t.thanksForChecking}</p>
      <svg className="tear" aria-hidden="true">
        <defs>
          <pattern id="torn-edge" width="20" height="14" patternUnits="userSpaceOnUse">
            <path d="M0 0L10 12L20 0Z" fill="var(--card)" />
            <path d="M0 0L10 12L20 0" fill="none" stroke="var(--line)" strokeWidth="2" strokeLinejoin="round" />
          </pattern>
        </defs>
        <rect width="100%" height="14" fill="url(#torn-edge)" />
      </svg>
    </article>
  );
}

function SendBack({ result: r, t }: { result: PublicResult; t: Labels }) {
  const [copied, setCopied] = useState(false);

  async function forward() {
    if (navigator.share) {
      try {
        await navigator.share({ text: r.correction });
        return;
      } catch (err) {
        if (err instanceof Error && err.name === "AbortError") return; // they closed the share sheet
      }
    }
    if (await copyText(r.correction)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    }
  }

  return (
    <section className="receipt-part">
      <h2 className="receipt-label" dir="auto">
        {r.forward_heading}
      </h2>
      <p className="field wrap-any whitespace-pre-wrap" dir="auto">
        {r.correction}
      </p>
      <div className="row mt-2">
        <button type="button" onClick={forward} className="btn btn-main">
          <Doodle name={copied ? "check" : "send"} size={26} />
          {copied ? t.copied : t.forwardCopy}
        </button>
        <a
          className="btn"
          href={`https://wa.me/?text=${encodeURIComponent(r.correction)}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <Doodle name="heart" size={22} />
          WhatsApp
        </a>
      </div>
    </section>
  );
}

function Extras({ id, result: r, t }: { id: string; result: PublicResult; t: Labels }) {
  async function share() {
    try {
      const res = await fetch(cardUrl(id));
      if (res.ok) {
        const file = new File([await res.blob()], "truefalse.png", { type: "image/png" });
        if (navigator.canShare?.({ files: [file] })) {
          await navigator.share({ files: [file], text: r.verdict_label });
          return;
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") return;
    }
    window.open(cardUrl(id), "_blank", "noopener");
  }

  function listen() {
    const said = new SpeechSynthesisUtterance(`${r.verdict_label}. ${r.explanation}`);
    said.lang = r.language;
    speechSynthesis.cancel();
    speechSynthesis.speak(said);
  }

  return (
    <div className="receipt-part row">
      <button type="button" onClick={listen} className="btn">
        <Doodle name="speaker" size={26} />
        {t.listen}
      </button>
      {!DEMO && (
        <button type="button" onClick={share} className="btn">
          <Doodle name="photo" size={26} />
          {t.sharePicture}
        </button>
      )}
    </div>
  );
}

function Feedback({ id, t }: { id: string; t: Labels }) {
  const [state, setState] = useState<"none" | "reporting" | "thanks" | "reported">("none");
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [sendFailed, setSendFailed] = useState(false);

  async function rate(rating: "up" | "down") {
    setSending(true);
    setSendFailed(false);
    try {
      await sendFeedback(id, rating, rating === "down" ? note : "");
      setState(rating === "up" ? "thanks" : "reported");
    } catch {
      setSendFailed(true);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="receipt-part small" aria-live="polite">
      {state === "thanks" && <p>{t.thanks}</p>}
      {state === "reported" && <p>{t.reported}</p>}
      {state === "none" && (
        <div className="row">
          <span>{t.helpful}</span>
          <button type="button" onClick={() => rate("up")} disabled={sending} aria-label={t.yes} className="round">
            <Doodle name="thumb" size={28} />
          </button>
          <button
            type="button"
            onClick={() => {
              setSendFailed(false);
              setState("reporting");
            }}
            disabled={sending}
            className="btn"
          >
            <Doodle name="flag" size={24} />
            {t.reportMistake}
          </button>
        </div>
      )}
      {state === "reporting" && (
        <div>
          <label htmlFor="note" className="mb-2 block">
            {t.whatWrong}
          </label>
          <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={2000}
            dir="auto" className="field" />
          <button type="button" onClick={() => rate("down")} disabled={sending} className="btn btn-main mt-2">
            {t.send}
          </button>
        </div>
      )}
      {sendFailed && (
        <p role="alert" className="alert mt-2">
          {t.feedbackFailed}
        </p>
      )}
    </section>
  );
}
