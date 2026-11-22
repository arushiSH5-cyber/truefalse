import CheckForm from "@/components/CheckForm";
import DemoExamples from "@/components/DemoExamples";
import InstallCard from "@/components/InstallCard";
import PhoneQR from "@/components/PhoneQR";
import { HomeIntro, HomeTip, Legend } from "@/components/HomeIntro";
import { DEMO } from "@/lib/demo";

export default function Home() {
  return (
    <div className="stack">
      <HomeIntro />
      {DEMO ? <DemoExamples /> : <CheckForm />}
      <Legend />
      <InstallCard />
      {DEMO && <PhoneQR />}
      {!DEMO && <HomeTip />}
    </div>
  );
}
