import CheckForm from "@/components/CheckForm";
import DemoExamples from "@/components/DemoExamples";
import PhoneQR from "@/components/PhoneQR";
import { HomeIntro, Legend } from "@/components/HomeIntro";
import { DEMO } from "@/lib/demo";

export default function Home() {
  return (
    <div className="stack">
      <HomeIntro />
      {DEMO ? <DemoExamples /> : <CheckForm />}
      <Legend />
      {DEMO && <PhoneQR />}
    </div>
  );
}
