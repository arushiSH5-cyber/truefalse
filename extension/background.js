// Right-click menu: check the selected text, a link, an image or the whole page.

import { explainError, isWebUrl, openResult, submitCheck } from "./api.js";

const MAX_IMAGE_BYTES = 20 * 1024 * 1024;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "selection", title: "Check “%s” with TrueFalse", contexts: ["selection"] });
    chrome.contextMenus.create({ id: "link", title: "Check this link with TrueFalse", contexts: ["link"] });
    chrome.contextMenus.create({ id: "image", title: "Check this image with TrueFalse", contexts: ["image"] });
    chrome.contextMenus.create({ id: "page", title: "Check this page with TrueFalse", contexts: ["page"] });
  });
});

/**
 * Download the image so the backend can read it; null if the site doesn't allow it.
 * activeTab covers the page's own origin, not CDNs: for those the backend fetches the link.
 */
async function fetchImage(src) {
  try {
    const res = await fetch(src);
    if (!res.ok) return null;
    const blob = await res.blob();
    if (!blob.type.startsWith("image/") || blob.size > MAX_IMAGE_BYTES) return null;
    const name = new URL(src).pathname.split("/").pop() || "image";
    return { file: blob, filename: name.slice(0, 100) };
  } catch {
    return null;
  }
}

function imageLink(src) {
  if (isWebUrl(src)) return { url: src };
  throw new Error(
    "Chrome won't let TrueFalse read this image. Take a screenshot of it and upload it in the web app."
  );
}

async function inputFor(info, tab) {
  switch (info.menuItemId) {
    case "selection":
      // Not the page URL: the backend would read the whole article and check that instead.
      return { text: info.selectionText };
    case "link":
      return { url: info.linkUrl };
    case "image":
      if (info.srcUrl?.startsWith("data:")) {
        const blob = await (await fetch(info.srcUrl)).blob();
        return { file: blob, filename: "image" };
      }
      return (await fetchImage(info.srcUrl)) ?? imageLink(info.srcUrl);
    default: {
      const url = info.pageUrl || tab?.url;
      if (!isWebUrl(url)) throw new Error("TrueFalse can only check http and https pages.");
      return { url };
    }
  }
}

async function showError(message) {
  await chrome.storage.session.set({ lastError: message });
  await chrome.windows.create({
    url: chrome.runtime.getURL("result.html?error=1"),
    type: "popup",
    width: 440,
    height: 320,
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    const id = await submitCheck(await inputFor(info, tab));
    await openResult(id);
  } catch (err) {
    await showError(explainError(err));
  }
});
