/** Common myths baked into the app so "For you" shows something at once, even offline or while
 * the checker wakes up. Generated from backend/app/myths.py (each read against its source page). */

export interface BakedMyth {
  myth: string;
  truth: string;
  publisher: string;
  url: string;
  topic: string;
}

export const BAKED_MYTHS: BakedMyth[] = [
  {
    "myth": "The MMR vaccine causes autism.",
    "truth": "Research has found no link between the MMR vaccine and autism.",
    "publisher": "NHS",
    "url": "https://www.nhs.uk/vaccinations/mmr-vaccine/",
    "topic": "Vaccines"
  },
  {
    "myth": "Herbal remedies can cure cancer.",
    "truth": "No herbal product has been shown to work as a cancer treatment.",
    "publisher": "National Cancer Institute",
    "url": "https://www.cancer.gov/about-cancer/causes-prevention/risk/myths",
    "topic": "Health"
  },
  {
    "myth": "Eating sugar makes cancer grow faster.",
    "truth": "Studies don't show that sugar makes cancer worse or that cutting it shrinks tumors.",
    "publisher": "National Cancer Institute",
    "url": "https://www.cancer.gov/about-cancer/causes-prevention/risk/myths",
    "topic": "Health"
  },
  {
    "myth": "Cell phones cause cancer.",
    "truth": "Phones give off low-energy radio waves that don't damage genes.",
    "publisher": "National Cancer Institute",
    "url": "https://www.cancer.gov/about-cancer/causes-prevention/risk/myths",
    "topic": "Technology"
  },
  {
    "myth": "Antiperspirants and deodorants cause breast cancer.",
    "truth": "The best studies found no link between them and breast cancer.",
    "publisher": "National Cancer Institute",
    "url": "https://www.cancer.gov/about-cancer/causes-prevention/risk/myths",
    "topic": "Health"
  },
  {
    "myth": "Antibiotics cure colds, flu and COVID-19.",
    "truth": "Antibiotics only work on bacteria. Colds, flu and COVID-19 are caused by viruses.",
    "publisher": "WHO",
    "url": "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
    "topic": "Health"
  },
  {
    "myth": "Drinking alcohol, methanol or bleach protects you from COVID-19.",
    "truth": "They don't prevent or cure it. Methanol and bleach are poisons that can kill.",
    "publisher": "WHO",
    "url": "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
    "topic": "Health"
  },
  {
    "myth": "Mosquitoes can spread COVID-19.",
    "truth": "COVID-19 is a respiratory virus that spreads between people, not through bites.",
    "publisher": "WHO",
    "url": "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
    "topic": "Health"
  },
  {
    "myth": "Adding pepper to your food prevents COVID-19.",
    "truth": "Spicy food doesn't prevent or cure it. Distance and clean hands protect you.",
    "publisher": "WHO",
    "url": "https://www.who.int/emergencies/diseases/novel-coronavirus-2019/advice-for-public/myth-busters",
    "topic": "Health"
  },
  {
    "myth": "Microwaves make food radioactive.",
    "truth": "Microwave energy turns into heat. It doesn't make food radioactive.",
    "publisher": "FDA",
    "url": "https://www.fda.gov/radiation-emitting-products/resources-you-radiation-emitting-products/microwave-oven-radiation",
    "topic": "Science"
  },
  {
    "myth": "Lightning never strikes the same place twice.",
    "truth": "It often does, especially tall, pointed, isolated things.",
    "publisher": "US National Weather Service",
    "url": "https://www.weather.gov/safety/lightning-myths",
    "topic": "Safety"
  },
  {
    "myth": "A tree is a safe place to shelter from lightning.",
    "truth": "Standing under a tree is one of the leading causes of lightning injuries.",
    "publisher": "US National Weather Service",
    "url": "https://www.weather.gov/safety/lightning-myths",
    "topic": "Safety"
  },
  {
    "myth": "We only use 10% of our brains.",
    "truth": "We use virtually all of the brain, and most of it is active almost all the time.",
    "publisher": "Scientific American",
    "url": "https://www.scientificamerican.com/article/do-people-only-use-10-percent-of-their-brains/",
    "topic": "Science"
  },
  {
    "myth": "The Great Wall of China is visible from the Moon.",
    "truth": "No human-made structure can be seen from the Moon with the naked eye.",
    "publisher": "Scientific American",
    "url": "https://www.scientificamerican.com/article/is-chinas-great-wall-visible-from-space/",
    "topic": "Science"
  },
  {
    "myth": "Cracking your knuckles gives you arthritis.",
    "truth": "Studies haven't found that it raises your risk of arthritis.",
    "publisher": "Harvard Health",
    "url": "https://www.health.harvard.edu/pain/does-cracking-knuckles-cause-arthritis",
    "topic": "Health"
  }
];
