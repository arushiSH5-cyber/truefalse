import Link from "next/link";
import { BackLink } from "@/components/HomeIntro";
import ResultView from "@/components/ResultView";

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
