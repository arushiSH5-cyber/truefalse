import Link from "next/link";
import { BackLink } from "@/components/HomeIntro";
import ResultView from "@/components/ResultView";

export default function ResultPage({ params }: { params: { id: string } }) {
  return (
    <div className="space-y-6">
      <Link href="/" className="text-sm underline">
        <BackLink />
      </Link>
      <ResultView id={params.id} />
    </div>
  );
}
