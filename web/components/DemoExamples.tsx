"use client";

import Link from "next/link";
import Doodle from "@/components/Doodle";
import { DEMO_EXAMPLES, REPO_URL } from "@/lib/demo";
import { VERDICTS } from "@/lib/verdicts";

/** Shown instead of the check form in the demo build. */
export default function DemoExamples() {
  return (
    <section className="stack">
      <div className="notice">
        <Doodle name="star" size={34} />
        <p>
          <strong>Demo.</strong> This online version shows example answers only. To check your own
          messages, run TrueFalse yourself:{" "}
          <a href={REPO_URL} className="underline">
            setup on GitHub
          </a>
          .
        </p>
      </div>
      <h2 className="receipt-label">Try an example</h2>
      <ul className="history">
        {DEMO_EXAMPLES.map((ex) => (
          <li key={ex.id} className={VERDICTS[ex.result.verdict].pattern}>
            <Link href={`/result/${ex.id}`}>
              <Doodle name={VERDICTS[ex.result.verdict].buddy} size={62} />
              <span>
                <span className="small block">{ex.language}</span>
                <span className="block wrap-any" dir="auto">
                  {ex.text}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
