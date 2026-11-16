// Renders a job record (GET /api/check/{id}) into the result and popup pages.

import { getJob, sendFeedback } from "./api.js";

const STYLE = {
  true: { emoji: "🟢", cls: "v-true" },
  misleading: { emoji: "🟠", cls: "v-misleading" },
  false: { emoji: "🔴", cls: "v-false" },
  unverified: { emoji: "⚪", cls: "v-unverified" },
};
const POLL_MS = 2000;
const GIVE_UP_MS = 6 * 60 * 1000;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function feedbackBox(id) {
  const box = el("section", { class: "feedback", "aria-live": "polite" });
  const done = (msg) => box.replaceChildren(el("p", {}, msg));
  const up = el("button", { onclick: async () => {
    await sendFeedback(id, "up").catch(() => {});
    done("Thanks for the feedback!");
  } }, "👍");
  const down = el("button", { onclick: () => {
    const note = el("textarea", { rows: "3", maxlength: "2000", dir: "auto", placeholder: "What was wrong? (optional)" });
    const send = el("button", { class: "primary", onclick: async () => {
      await sendFeedback(id, "down", note.value).catch(() => {});
      done("Thanks. A person will review this answer.");
    } }, "Send");
    box.replaceChildren(note, send);
  } }, "👎 Report a mistake");
  box.append(el("span", {}, "Was this helpful? "), up, down);
  return box;
}

function renderResult(root, id, r) {
  const s = STYLE[r.verdict] ?? STYLE.unverified;
  const card = el("article", { lang: r.language });
  card.append(el("header", { class: `band ${s.cls}`, dir: "auto" }, `${s.emoji} ${r.verdict_label}`));
  if (r.claims[0]) card.append(el("p", { class: "claim", dir: "auto" }, `“${r.claims[0]}”`));
  card.append(el("p", { class: "explanation", dir: "auto" }, r.explanation));
  card.append(el("p", { class: "confidence", dir: "auto" }, r.confidence_label || `Confidence: ${r.confidence}`));
  if (r.needs_human_review) {
    card.append(el("p", { class: "note" }, "👥 Our checks did not fully agree, so a person will review this answer."));
  }
  if (r.warnings.length) {
    card.append(el("ul", { class: "warnings", dir: "auto" }, ...r.warnings.map((w) => el("li", {}, `⚠️ ${w}`))));
  }
  if (r.correction) {
    const copy = el("button", { class: "primary", onclick: async () => {
      await navigator.clipboard.writeText(r.correction).catch(() => {});
      copy.textContent = "✓ Copied";
    } }, "Copy to forward");
    card.append(el("section", { class: "forward" },
      el("h2", { dir: "auto" }, `📤 ${r.forward_heading}`),
      el("p", { dir: "auto" }, r.correction),
      copy));
  }
  if (r.sources.length) {
    card.append(el("h2", { dir: "auto" }, r.sources_heading),
      el("ol", { class: "sources" }, ...r.sources.map((src) =>
        el("li", {}, el("a", { href: src.url, target: "_blank", rel: "noopener noreferrer" }, src.title),
          src.publisher ? ` · ${src.publisher}` : ""))));
  }
  card.append(feedbackBox(id));
  root.replaceChildren(card);
}

/** Poll a job until it finishes, rendering progress and then the answer. */
export async function showJob(root, id) {
  const started = Date.now();
  root.replaceChildren(el("p", { class: "status" }, "🔎 Checking…"));
  for (;;) {
    let rec;
    try {
      rec = await getJob(id);
    } catch {
      rec = undefined; // network blip: keep polling
    }
    if (rec === null) {
      root.replaceChildren(el("p", {}, "This result has expired or does not exist."));
      return;
    }
    if (rec?.status === "done" && rec.result) return renderResult(root, id, rec.result);
    if (rec?.status === "failed" || (rec?.status === "done" && !rec.result)) {
      root.replaceChildren(el("p", {}, "Sorry, something went wrong while checking this. Please try again."));
      return;
    }
    const last = rec?.messages?.at(-1);
    root.replaceChildren(el("p", { class: "status" }, "🔎 Checking…"), last ? el("p", { class: "muted", dir: "auto" }, last) : null);
    if (Date.now() - started > GIVE_UP_MS) {
      root.append(el("p", {}, "This is taking longer than usual. Try again in a minute."));
      return;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}
