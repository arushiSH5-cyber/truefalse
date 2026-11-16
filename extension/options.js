import { DEFAULT_API, apiUrl } from "./api.js";

const input = document.getElementById("api");
const status = document.getElementById("status");
input.value = await apiUrl();

document.getElementById("form").addEventListener("submit", async (e) => {
  e.preventDefault();
  let url;
  try {
    url = new URL(input.value.trim() || DEFAULT_API);
  } catch {
    status.textContent = "That is not a valid address.";
    return;
  }
  const origin = `${url.protocol}//${url.host}/*`;
  // Chrome asks the user before the extension may talk to a new server.
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) {
    status.textContent = "Permission was not granted, so the address was not saved.";
    return;
  }
  await chrome.storage.sync.set({ apiUrl: url.origin });
  status.textContent = `Saved: ${url.origin}`;
});
