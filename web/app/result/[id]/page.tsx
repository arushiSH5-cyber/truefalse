import Link from "next/link";
import ResultView from "@/components/ResultView";

export default function ResultPage({ params }: { params: { id: string } }) {
  return (
    <div className="space-y-6">
      <Link href="/" className="text-sm underline">
        ← Check something else
      </Link>
      <ResultView id={params.id} />
    </div>
  );
}
