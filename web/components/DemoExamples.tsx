"use client";

import Link from "next/link";
import { DEMO_EXAMPLES, REPO_URL } from "@/lib/demo";

/** Shown instead of the check form in the demo build. */
export default function DemoExamples() {
  return (
    <section className="space-y-3">
      <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
        <strong>Demo.</strong> This online version shows example answers only. To check your own
        messages, run TrueFalse yourself:{" "}
        <a href={REPO_URL} className="underline">
          setup on GitHub
        </a>
        .
      </div>
      <h2 className="text-sm font-semibold">Try an example</h2>
      <ul className="space-y-2">
        {DEMO_EXAMPLES.map((ex) => (
          <li key={ex.id}>
            <Link
              href={`/result/${ex.id}`}
              className="block rounded-xl border border-slate-300 bg-white p-3 shadow-sm hover:border-slate-500 dark:border-slate-700 dark:bg-slate-900"
            >
              <span className="block text-xs text-slate-500">{ex.language}</span>
              <span className="block" dir="auto">
                {ex.text}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
