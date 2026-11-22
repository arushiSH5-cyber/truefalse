// Renders a job record (GET /api/check/{id}) into the result and popup pages.

import { getJob, sendFeedback } from "./api.js";

const STYLE = {
  true: { buddy: "mochi-happy", badge: "check", cls: "v-true", label: "True" },
  misleading: { buddy: "pudding-think", badge: "wavy", cls: "v-misleading", label: "Misleading" },
  false: { buddy: "berry-wow", badge: "cross", cls: "v-false", label: "False" },
  unverified: { buddy: "luna-sleep", badge: "question", cls: "v-unverified", label: "Unverified" },
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

function doodle(name, size, alt = "") {
  return el("img", { src: `doodles/${name}.svg`, width: String(size), height: String(size), alt });
}

function checking() {
  return el("p", { class: "status" }, doodle("scout-think", 120), "Checking…");
}

function feedbackBox(id) {
  const box = el("section", { class: "feedback", "aria-live": "polite" });
  const error = el("p", { class: "error", role: "alert" });

  async function send(button, rating, note, thanks) {
    button.disabled = true;
    error.textContent = "";
    try {
      await sendFeedback(id, rating, note);
      box.replaceChildren(el("p", {}, thanks));
    } catch {
      error.textContent = "Could not send your feedback. Please try again.";
      button.disabled = false;
    }
  }

  const up = el("button", { onclick: () => send(up, "up", "", "Thanks for the feedback!") }, doodle("thumb", 26, "Yes"));
  const down = el("button", { onclick: () => {
    const note = el("textarea", { rows: "3", maxlength: "2000", dir: "auto", placeholder: "What was wrong? (optional)" });
    const submit = el("button", {
      class: "primary",
      onclick: () => send(submit, "down", note.value, "Thanks. A person will review this answer."),
    }, "Send");
    error.textContent = "";
    box.replaceChildren(note, submit, error);
  } }, doodle("flag", 22), "Report a mistake");
  box.append(el("span", {}, "Was this helpful? "), up, down, error);
  return box;
}

/** One claim: quote it. Several: list each with its own verdict, never under the overall one. */
function claimsBlock(r) {
  if (r.claims.length < 2) {
    return r.claims[0] ? el("p", { class: "claim", dir: "auto" }, `“${r.claims[0]}”`) : null;
  }
  return el("ul", { class: "claims" }, ...r.claims.map((claim, i) => {
    const verdict = r.claim_results?.[i]?.verdict;
    const s = STYLE[verdict];
    return el("li", {},
      s ? el("strong", {}, doodle(s.badge, 24), ` ${s.label}`) : null,
      el("span", { dir: "auto" }, `“${claim}”`));
  }));
}

function renderResult(root, id, r) {
  const s = STYLE[r.verdict] ?? STYLE.unverified;
  const card = el("article", { lang: r.language });
  card.append(el("header", { class: `band ${s.cls}`, dir: "auto" },
    doodle(s.buddy, 104), el("span", { class: "stamp" }, r.verdict_label)));
  const claims = claimsBlock(r);
  if (claims) card.append(claims);
  card.append(el("p", { class: "explanation", dir: "auto" }, r.explanation));
  card.append(el("p", { class: "confidence", dir: "auto" }, r.confidence_label || `Confidence: ${r.confidence}`));
  if (r.needs_human_review) {
    card.append(el("p", { class: "note" }, "Our checks did not fully agree, so a person will review this answer."));
  }
  if (r.warnings.length) {
    card.append(el("ul", { class: "warnings", dir: "auto" }, ...r.warnings.map((w) => el("li", {}, doodle("warning", 20), ` ${w}`))));
  }
  if (r.correction) {
    const copy = el("button", { class: "primary", onclick: async () => {
      await navigator.clipboard.writeText(r.correction).catch(() => {});
      copy.textContent = "Copied!";
    } }, "Copy to forward");
    card.append(el("section", { class: "forward" },
      el("h2", { dir: "auto" }, doodle("send", 22), ` ${r.forward_heading}`),
      el("p", { dir: "auto" }, r.correction),
      copy));
  }
  if (r.sources.length) {
    card.append(el("h2", { dir: "auto" }, r.sources_heading),
      el("ol", { class: "sources" }, ...r.sources.map((src) =>
        el("li", {}, /^https?:\/\//i.test(src.url)
          ? el("a", { href: src.url, target: "_blank", rel: "noopener noreferrer" }, src.title)
          : el("span", {}, src.title),
          src.publisher ? ` · ${src.publisher}` : ""))));
  }
  card.append(feedbackBox(id));
  root.replaceChildren(card);
}

/** Poll a job until it finishes, rendering progress and then the answer. */
export async function showJob(root, id) {
  const started = Date.now();
  root.replaceChildren(checking());
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
    root.replaceChildren(checking(), last ? el("p", { class: "muted", dir: "auto" }, last) : "");
    if (Date.now() - started > GIVE_UP_MS) {
      root.append(el("p", {}, "This is taking longer than usual. Try again in a minute."));
      return;
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}
