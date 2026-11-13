import type { Metadata, Viewport } from "next";
import { Analytics } from "@vercel/analytics/next";
import { region } from "@/data/regions";
import { site } from "@/data/site";
import { siteUrl } from "@/lib/site-url";
import "./globals.css";

const title = `${site.shortName} | AI Automation Engineer: n8n, Claude, RAG`;
const description = region.seo.description;

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: { default: title, template: `%s | ${site.shortName}` },
  description,
  applicationName: site.name,
  authors: [{ name: site.name, url: "/" }],
  creator: site.name,
  keywords: ["AI automation engineer", "AI engineer", "automation specialist", "n8n developer", "n8n", "Claude API", "RAG", "call QA",
             "LLM evals", "MCP", "automation developer", ...region.seo.keywords],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    url: "/",
    title,
    description,
    siteName: site.name,
    locale: "en_US",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: `${site.name}, ${site.title}` }],
  },
  twitter: { card: "summary_large_image", title, description, images: ["/og.png"] },
};

export const viewport: Viewport = {
  // The site has its own light and dark themes, so browsers should not force-darken it.
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafaf8" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0e11" },
  ],
};

// Runs before paint so the page never flashes the wrong theme.
const themeScript = `(function(){var d=window.matchMedia('(prefers-color-scheme: dark)').matches;try{var t=localStorage.getItem('theme');if(t==='dark'||t==='light')d=t==='dark'}catch(e){}document.documentElement.classList.toggle('dark',d)})()`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh antialiased">
        {children}
        <Analytics />
      </body>
    </html>
  );
}
