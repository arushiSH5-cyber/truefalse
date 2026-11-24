"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import Doodle from "@/components/Doodle";
import { useLabels } from "@/lib/i18n";

export function TopBar() {
  const t = useLabels();
  const [night, setNight] = useState(false);

  useEffect(() => setNight(document.documentElement.dataset.theme === "night"), []);

  function flip() {
    const theme = night ? "day" : "night";
    document.documentElement.dataset.theme = theme;
    setNight(!night);
    try {
      localStorage.setItem("truefalse.theme", theme);
    } catch {
      /* not remembered, still switched */
    }
  }

  return (
    <header className="topbar">
      <Link href="/" className="wordmark" dir="ltr">
        <Doodle name="scout-happy" size={48} />
        TrueFalse
      </Link>
      <button type="button" className="round" onClick={flip} aria-label={night ? t.dayMode : t.nightMode}>
        <Doodle name={night ? "sun" : "moon"} size={34} />
      </button>
    </header>
  );
}

export function Tabs() {
  const t = useLabels();
  const at = usePathname();
  const tabs = [
    { href: "/", icon: "magnifier", label: t.navCheck, here: at === "/" || at.startsWith("/result") },
    { href: "/for-you", icon: "heart", label: t.navForYou, here: at.startsWith("/for-you") },
    { href: "/history", icon: "book", label: t.navHistory, here: at.startsWith("/history") },
  ];
  return (
    <nav className="tabs">
      {tabs.map((tab) => (
        <Link key={tab.href} href={tab.href} aria-current={tab.here ? "page" : undefined}>
          <Doodle name={tab.icon} size={32} />
          {tab.label}
        </Link>
      ))}
    </nav>
  );
}
