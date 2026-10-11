import CheckForm from "@/components/CheckForm";

export default function Home() {
  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">TrueFalse</h1>
        <p className="text-slate-600 dark:text-slate-400">
          Got a forward you are not sure about? Check it here, in any language. We look at
          fact-checkers and trusted sources like WHO, PubMed and news agencies.
        </p>
        <p className="text-sm">
          <span className="mr-2">🟢 True</span>
          <span className="mr-2">🟠 Misleading</span>
          <span className="mr-2">🔴 False</span>
          <span>⚪ Unverified</span>
        </p>
      </header>
      <CheckForm />
      <p className="text-xs text-slate-500">
        Tip: install this app, then use your phone&apos;s Share button in WhatsApp, Instagram,
        YouTube or any app to send content straight here. We don&apos;t keep your messages.
      </p>
    </div>
  );
}
