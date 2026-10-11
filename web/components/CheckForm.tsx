"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { submitCheck } from "@/lib/api";

const MAX_BYTES = 20 * 1024 * 1024;

export default function CheckForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!text.trim() && !file) {
      setError("Paste a message or link, or add a photo, voice note or video.");
      return;
    }
    if (file && file.size > MAX_BYTES) {
      setError("That file is too big (max 20 MB).");
      return;
    }
    const form = new FormData();
    form.set("text", text);
    if (file) form.set("file", file);
    setBusy(true);
    try {
      router.push(`/result/${await submitCheck(form)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <label htmlFor="text" className="block text-sm font-medium">
        Message, rumor or link
      </label>
      <textarea
        id="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={6}
        dir="auto"
        placeholder="Paste anything you were forwarded…"
        className="w-full rounded-xl border border-slate-300 bg-white p-3 text-base shadow-sm focus:outline-none focus:ring-2 focus:ring-slate-500 dark:border-slate-700 dark:bg-slate-900"
      />
      <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-slate-400 p-3 text-sm dark:border-slate-600">
        <span aria-hidden>📎</span>
        <span className="truncate">{file ? file.name : "Add a photo, screenshot, voice note or video"}</span>
        <input
          type="file"
          accept="image/*,audio/*,video/*"
          className="sr-only"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-xl bg-slate-900 px-4 py-3 text-lg font-semibold text-white disabled:opacity-60 dark:bg-white dark:text-slate-900"
      >
        {busy ? "Sending…" : "Check it"}
      </button>
    </form>
  );
}
