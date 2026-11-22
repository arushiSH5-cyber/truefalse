import Link from "next/link";
import Doodle from "@/components/Doodle";
import { BackLink } from "@/components/HomeIntro";
import ResultView from "@/components/ResultView";
import { DEMO, DEMO_EXAMPLES } from "@/lib/demo";

// The demo build is static: one page per example, plus the page that opens saved checks.
export function generateStaticParams() {
  return DEMO ? [...DEMO_EXAMPLES.map((e) => ({ id: e.id })), { id: "saved" }] : [];
}

export default async function ResultPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="stack">
      <Link href="/" className="btn">
        <Doodle name="back" size={28} />
        <BackLink />
      </Link>
      <ResultView id={id} />
    </div>
  );
}
