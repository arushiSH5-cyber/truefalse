export type InstallState = "installed" | "ready" | "manual";

interface InstallPrompt extends Event {
  prompt(): Promise<void>;
}

let prompt: InstallPrompt | null = null;
const listeners = new Set<(s: InstallState) => void>();

function state(): InstallState {
  if (matchMedia("(display-mode: standalone)").matches) return "installed";
  return prompt ? "ready" : "manual";
}
const tell = () => listeners.forEach((fn) => fn(state()));

/** Chrome fires this once, early, so it's caught in the layout, not where the button is. */
export function captureInstall(): () => void {
  const onPrompt = (e: Event) => {
    e.preventDefault();
    prompt = e as InstallPrompt;
    tell();
  };
  const onInstalled = () => {
    prompt = null;
    tell();
  };
  window.addEventListener("beforeinstallprompt", onPrompt);
  window.addEventListener("appinstalled", onInstalled);
  return () => {
    window.removeEventListener("beforeinstallprompt", onPrompt);
    window.removeEventListener("appinstalled", onInstalled);
  };
}

export function watchInstall(fn: (s: InstallState) => void): () => void {
  listeners.add(fn);
  fn(state());
  return () => {
    listeners.delete(fn);
  };
}

export async function install() {
  await prompt?.prompt();
  prompt = null;
  tell();
}
