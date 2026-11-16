import CheckForm from "@/components/CheckForm";
import { HomeIntro, HomeTip } from "@/components/HomeIntro";

export default function Home() {
  return (
    <div className="space-y-6">
      <HomeIntro />
      <CheckForm />
      <HomeTip />
    </div>
  );
}
