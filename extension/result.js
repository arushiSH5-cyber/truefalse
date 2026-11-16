import { showJob } from "./render.js";

const root = document.getElementById("root");
const params = new URLSearchParams(location.search);
if (params.get("error")) {
  const { lastError } = await chrome.storage.session.get("lastError");
  const p = document.createElement("p");
  p.className = "error";
  p.textContent = lastError || "Something went wrong.";
  root.replaceChildren(p);
} else {
  showJob(root, params.get("id") || "");
}
