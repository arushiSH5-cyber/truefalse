import { explainError, isWebUrl, submitCheck } from "./api.js";
import { showJob } from "./render.js";

const form = document.getElementById("form");
const error = document.getElementById("error");
const root = document.getElementById("root");

async function run(input) {
  error.textContent = "";
  try {
    const id = await submitCheck(input);
    form.hidden = true;
    await showJob(root, id);
  } catch (err) {
    error.textContent = explainError(err);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = document.getElementById("text").value.trim();
  if (!text) {
    error.textContent = "Paste a message or link first.";
    return;
  }
  run({ text });
});

document.getElementById("page").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  // tab.url comes from activeTab, and is missing on pages Chrome keeps private.
  if (isWebUrl(tab?.url)) run({ url: tab.url });
  else error.textContent = "This page can't be checked. Copy the text and paste it here instead.";
});
