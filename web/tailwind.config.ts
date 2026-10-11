import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        verdict: {
          true: "#1f8a4c",
          misleading: "#d97706",
          false: "#c0392b",
          unverified: "#6b7280",
        },
      },
    },
  },
  plugins: [],
};
export default config;
