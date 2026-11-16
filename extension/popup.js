import { submitCheck } from "./api.js";
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
    error.textContent = `${err.message}. Is the TrueFalse server running? Check Settings.`;
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
  if (tab?.url) run({ url: tab.url });
});
