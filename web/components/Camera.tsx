"use client";

import { useEffect, useRef, useState } from "react";
import Doodle from "@/components/Doodle";
import { useLabels } from "@/lib/i18n";

/** Live camera for laptops. Phones use their own camera app through a file input. */
export default function Camera({ onPhoto, onClose }: { onPhoto: (f: File) => void; onClose: () => void }) {
  const t = useLabels();
  const dialog = useRef<HTMLDialogElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<"starting" | "live" | "failed">("starting");

  useEffect(() => {
    let stream: MediaStream | undefined;
    let closed = false;
    if (!dialog.current?.open) dialog.current?.showModal();
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 } } })
      .then((s) => {
        if (closed) return s.getTracks().forEach((track) => track.stop());
        stream = s;
        if (video.current) video.current.srcObject = s;
      })
      .catch(() => {
        if (!closed) setState("failed");
      });
    return () => {
      closed = true;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  function snap() {
    const v = video.current;
    if (!v?.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext("2d")?.drawImage(v, 0, 0);
    canvas.toBlob(
      (blob) => blob && onPhoto(new File([blob], "camera.jpg", { type: "image/jpeg" })),
      "image/jpeg",
      0.92,
    );
  }

  return (
    <dialog ref={dialog} className="camera" onCancel={onClose} aria-label={t.takePhoto}>
      {state === "failed" ? (
        <div className="waiting" role="alert">
          <Doodle name="scout-sad" size={120} />
          <p>{t.cameraBlocked}</p>
        </div>
      ) : (
        <video ref={video} autoPlay playsInline muted onLoadedData={() => setState("live")} />
      )}
      <div className="row mt-3 justify-center">
        {state !== "failed" && (
          <button type="button" className="btn btn-main" onClick={snap} disabled={state !== "live"}>
            <Doodle name="camera" size={28} />
            {t.snap}
          </button>
        )}
        <button type="button" className="btn" onClick={onClose}>
          {t.cancel}
        </button>
      </div>
    </dialog>
  );
}
