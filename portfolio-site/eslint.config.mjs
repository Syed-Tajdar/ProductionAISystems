// ESLint for the site: Next.js rules (React, hooks, accessibility, Core Web Vitals) and its TypeScript rules.
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  { ignores: [".next/**", "out/**", "next-env.d.ts"] },
];

export default config;
