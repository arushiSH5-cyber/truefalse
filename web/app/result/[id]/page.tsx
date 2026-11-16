import Link from "next/link";
import { BackLink } from "@/components/HomeIntro";
import ResultView from "@/components/ResultView";
import { DEMO, DEMO_EXAMPLES } from "@/lib/demo";

// The demo build is static: one page per saved example.
export function generateStaticParams() {
  return DEMO ? DEMO_EXAMPLES.map((e) => ({ id: e.id })) : [];
}

export default async function ResultPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="space-y-6">
      <Link href="/" className="text-sm underline">
        <BackLink />
      </Link>
      <ResultView id={id} />
    </div>
  );
}
