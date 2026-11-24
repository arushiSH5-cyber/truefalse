"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import Camera from "@/components/Camera";
import Doodle from "@/components/Doodle";
import { getStatus, submitCheck } from "@/lib/api";
import { fitPhoto } from "@/lib/browser";
import { STATIC } from "@/lib/demo";
import { useLabels } from "@/lib/i18n";

const MAX_BYTES = 20 * 1024 * 1024;
const MAX_RECORD_MS = 3 * 60 * 1000;

export default function CheckForm() {
  const router = useRouter();
  const t = useLabels();
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [slowStart, setSlowStart] = useState(false);
  const [camera, setCamera] = useState(false);
  const [recording, setRecording] = useState(false);
  const [server, setServer] = useState<"ok" | "no-key" | "down">("ok");
  const photoInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const audioInput = useRef<HTMLInputElement>(null);
  const recorder = useRef<MediaRecorder | null>(null);

  useEffect(() => {
    // The share target sends people back here with ?error=<status>.
    const params = new URLSearchParams(location.search);
    if (params.has("error")) setError(t.shareFailed);
    // "Check it" on the For you tab opens this form with the headline filled in.
    // The static site's Share target also arrives here, as ?title=&text=&url=.
    const prefill = ["title", "text", "url"]
      .map((k) => params.get(k)?.trim())
      .filter(Boolean)
      .join("\n");
    if (prefill) setText(prefill.slice(0, 2000));
  }, [t]);

  useEffect(() => {
    const look = () => getStatus().then(setServer);
    look();
    // Coming back from the terminal after adding the key should clear the notice.
    window.addEventListener("focus", look);
    const again = setInterval(() => document.visibilityState === "visible" && look(), 5000);
    return () => {
      window.removeEventListener("focus", look);
      clearInterval(again);
    };
  }, []);

  useEffect(() => {
    if (!file?.type.startsWith("image/")) return setPreview(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  useEffect(() => {
    function paste(e: ClipboardEvent) {
      const image = Array.from(e.clipboardData?.files ?? []).find((f) => f.type.startsWith("image/"));
      if (!image) return;
      e.preventDefault();
      setFile(image);
    }
    window.addEventListener("paste", paste);
    return () => window.removeEventListener("paste", paste);
  }, []);

  useEffect(() => () => recorder.current?.stream.getTracks().forEach((track) => track.stop()), []);

  function pick(next: File | undefined) {
    if (!next) return;
    setError(null);
    setFile(next);
  }

  function takePhoto() {
    // Phones have a better camera app than anything in a page, and it works without HTTPS.
    const phone = matchMedia("(pointer: coarse)").matches;
    if (!phone && window.isSecureContext) setCamera(true);
    else photoInput.current?.click();
  }

  async function voice() {
    if (recording) return recorder.current?.stop();
    if (!window.isSecureContext || typeof MediaRecorder === "undefined") {
      return audioInput.current?.click();
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        setRecording(false);
        const type = (rec.mimeType || "audio/webm").split(";")[0];
        if (chunks.some((c) => c.size)) pick(new File(chunks, `voice-note.${type.split("/")[1]}`, { type }));
        else setError(t.micBlocked); // a muted or silent-failing mic records nothing
      };
      rec.start();
      recorder.current = rec;
      setRecording(true);
      setTimeout(() => rec.state === "recording" && rec.stop(), MAX_RECORD_MS);
    } catch {
      setError(t.micBlocked);
    }
  }

  // The free hosted checker sleeps when nobody uses it; the first check wakes it (about a minute).
  useEffect(() => {
    if (!busy) return setSlowStart(false);
    const timer = setTimeout(() => setSlowStart(true), 5000);
    return () => clearTimeout(timer);
  }, [busy]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!text.trim() && !file) return setError(t.emptyError);
    setBusy(true);
    try {
      const ready = file && (await fitPhoto(file));
      if (ready && ready.size > MAX_BYTES) {
        setBusy(false);
        return setError(t.tooBig);
      }
      const form = new FormData();
      form.set("text", text);
      if (ready) form.set("file", ready);
      const id = await submitCheck(form);
      router.push(STATIC ? `/result/saved/?id=${id}` : `/result/${id}`);
    } catch (err) {
      setError(err instanceof TypeError ? t.serverDown : err instanceof Error ? err.message : t.genericError);
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="stack"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        const dropped = e.dataTransfer.files[0];
        if (!dropped) return;
        e.preventDefault();
        pick(dropped);
      }}
    >
      {server !== "ok" && (
        <div className="notice" role="status">
          <Doodle name="scout-sleep" size={52} />
          <p className="wrap-any">{server === "down" ? t.serverDown : t.noKey}</p>
        </div>
      )}

      <div className="ways">
        <button type="button" className="way" onClick={takePhoto}>
          <Doodle name="camera" size={58} />
          {t.takePhoto}
        </button>
        <button type="button" className="way" onClick={() => fileInput.current?.click()}>
          <Doodle name="photo" size={58} />
          {t.upload}
        </button>
        <button type="button" className="way" onClick={voice} aria-pressed={recording}>
          <Doodle name="mic" size={58} className={recording ? "bob" : undefined} />
          {recording ? t.stopRecording : t.record}
        </button>
      </div>
      <input ref={photoInput} id="photo" type="file" hidden accept="image/*" capture="environment"
        onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      <input ref={fileInput} id="file" type="file" hidden accept="image/*,audio/*,video/*,.heic,.heif"
        onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      <input ref={audioInput} id="audio" type="file" hidden accept="audio/*"
        onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />

      <div className="card stack">
        {file && (
          <div>
            {preview && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={preview} alt="" className="preview" onError={() => setPreview(null)} />
            )}
            <div className="row mt-2 justify-between">
              <span className="small wrap-any min-w-0 flex-1" dir="auto">
                {file.name}
              </span>
              <button type="button" className="btn" onClick={() => setFile(null)}>
                <Doodle name="bin" size={24} />
                {t.remove}
              </button>
            </div>
          </div>
        )}
        <label htmlFor="text" className="receipt-label block">
          {t.inputLabel}
        </label>
        <textarea
          id="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={file ? 2 : 3}
          maxLength={10000}
          dir="auto"
          placeholder={t.placeholder}
          className="field"
        />
        {error && (
          <p role="alert" className="alert wrap-any">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || recording} className="btn btn-main btn-big">
          <Doodle name="magnifier" size={34} />
          {busy ? t.sending : t.checkIt}
        </button>
        {slowStart && (
          <p className="small soft" aria-live="polite">
            {t.wakingUp}
          </p>
        )}
      </div>

      {camera && (
        <Camera
          onClose={() => setCamera(false)}
          onPhoto={(photo) => {
            setCamera(false);
            pick(photo);
          }}
        />
      )}
    </form>
  );
}
