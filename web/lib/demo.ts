/**
 * Demo mode (NEXT_PUBLIC_DEMO=1): the public GitHub Pages build has no backend, so it shows
 * a few saved example answers instead of checking new messages. Every page says it's a demo.
 */
import type { JobRecord, PublicResult } from "@/lib/api";

export const DEMO = process.env.NEXT_PUBLIC_DEMO === "1";
export const REPO_URL = "https://github.com/arushiSH5-cyber/truefalse";

const WHO_MYTHS = {
  title: "Coronavirus disease (COVID-19) advice for the public: Mythbusters",
  url: "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
  publisher: "World Health Organization",
  tier: 1,
  published: null,
};

function result(r: Partial<PublicResult> & Pick<PublicResult, "verdict" | "verdict_label">): PublicResult {
  return {
    confidence: "high",
    language: "en",
    reply_text: "",
    explanation: "",
    correction: "",
    confidence_label: "",
    sources_heading: "Sources",
    forward_heading: "Forward this",
    warnings: [],
    sources: [],
    claims: [],
    needs_human_review: false,
    ai_overall: "no_signal",
    ai_signals: [],
    ...r,
  };
}

export interface DemoExample {
  id: string;
  language: string;
  text: string;
  result: PublicResult;
}

export const DEMO_EXAMPLES: DemoExample[] = [
  {
    id: "garlic",
    language: "English",
    text: "Eating garlic protects you from the coronavirus. Share with everyone!",
    result: result({
      verdict: "false",
      verdict_label: "False",
      claims: ["Eating garlic protects you from the coronavirus."],
      explanation:
        "Garlic is a healthy food, but there is no evidence that eating it protects people from the virus that causes COVID-19. The World Health Organization lists this as a myth.",
      confidence_label: "Confidence: High",
      correction: `Garlic does not protect you from COVID-19. The WHO lists this as a myth: ${WHO_MYTHS.url}`,
      sources: [WHO_MYTHS],
    }),
  },
  {
    id: "5g",
    language: "English",
    text: "5G mobile networks are spreading COVID-19.",
    result: result({
      verdict: "false",
      verdict_label: "False",
      claims: ["5G mobile networks spread COVID-19."],
      explanation:
        "Viruses cannot travel on radio waves or mobile networks. COVID-19 has also spread in many places that have no 5G network.",
      confidence_label: "Confidence: High",
      correction: `5G does not spread COVID-19. Viruses can't travel on radio waves: ${WHO_MYTHS.url}`,
      sources: [WHO_MYTHS],
    }),
  },
  {
    id: "limon",
    language: "Español",
    text: "El agua caliente con limón cura el cáncer. ¡Compártelo!",
    result: result({
      verdict: "false",
      verdict_label: "Falso",
      language: "es",
      claims: ["El agua caliente con limón cura el cáncer."],
      explanation:
        "No hay pruebas de que el agua caliente con limón cure el cáncer. Usar remedios caseros en lugar del tratamiento médico puede ser peligroso.",
      confidence_label: "Confianza: Alta",
      sources_heading: "Fuentes",
      forward_heading: "Reenvía esto",
      warnings: ["No soy médico. Consulta a un profesional de salud antes de cambiar un tratamiento."],
      correction:
        "El agua caliente con limón no cura el cáncer. Consulta siempre a un médico: https://www.cancer.gov/about-cancer/treatment/cam",
      sources: [
        {
          title: "Complementary and Alternative Medicine (CAM)",
          url: "https://www.cancer.gov/about-cancer/treatment/cam",
          publisher: "National Cancer Institute",
          tier: 1,
          published: null,
        },
      ],
    }),
  },
  {
    id: "haath",
    language: "हिंदी",
    text: "साबुन से हाथ धोने से कीटाणु फैलने से रुकते हैं।",
    result: result({
      verdict: "true",
      verdict_label: "सही",
      language: "hi",
      claims: ["साबुन से हाथ धोने से कीटाणु फैलने से रुकते हैं।"],
      explanation:
        "साबुन और पानी से अच्छी तरह हाथ धोना कीटाणुओं को फैलने से रोकने के सबसे आसान तरीकों में से एक है। विश्व स्वास्थ्य संगठन भी यही सलाह देता है।",
      confidence_label: "भरोसा: ऊँचा",
      sources_heading: "स्रोत",
      forward_heading: "इसे आगे भेजें",
      correction:
        "यह सही है: साबुन से हाथ धोने से कीटाणु कम फैलते हैं। https://www.who.int/campaigns/world-hand-hygiene-day",
      sources: [
        {
          title: "World Hand Hygiene Day",
          url: "https://www.who.int/campaigns/world-hand-hygiene-day",
          publisher: "World Health Organization",
          tier: 1,
          published: null,
        },
      ],
    }),
  },
];

export function demoJob(id: string): JobRecord | null {
  const ex = DEMO_EXAMPLES.find((e) => e.id === id);
  if (!ex) return null;
  return {
    id,
    status: "done",
    messages: [],
    result: { ...ex.result, reply_text: `${ex.result.verdict_label}\n\n${ex.result.explanation}` },
  };
}
