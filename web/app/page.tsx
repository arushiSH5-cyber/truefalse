import CheckForm from "@/components/CheckForm";
import DemoExamples from "@/components/DemoExamples";
import { HomeIntro, HomeTip } from "@/components/HomeIntro";
import { DEMO } from "@/lib/demo";

export default function Home() {
  return (
    <div className="space-y-6">
      <HomeIntro />
      {DEMO ? <DemoExamples /> : <CheckForm />}
      {!DEMO && <HomeTip />}
    </div>
  );
}
