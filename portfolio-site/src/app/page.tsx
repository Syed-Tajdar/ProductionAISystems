import { Nav } from "@/components/Nav";
import { Hero } from "@/components/Hero";
import { Projects } from "@/components/Projects";
import { Services } from "@/components/Services";
import { Recruiters } from "@/components/Recruiters";
import { DemoJump } from "@/components/DemoJump";
import { HowIBuild } from "@/components/HowIBuild";
import { MiniAutomations } from "@/components/MiniAutomations";
import { Skills } from "@/components/Skills";
import { Experience } from "@/components/Experience";
import { Contact } from "@/components/Contact";
import { Footer } from "@/components/Footer";
import { JsonLd } from "@/components/JsonLd";
import { homeGraph } from "@/lib/schema";

export default function Home() {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-accent-fg"
      >
        Skip to content
      </a>
      <JsonLd data={homeGraph} />
      <Nav />
      <main id="main">
        <Hero />
        <Services />
        <Projects />
        <Experience />
        <Skills />
        <Recruiters />
        <MiniAutomations />
        <HowIBuild />
        <Contact />
      </main>
      <Footer />
      <DemoJump />
    </>
  );
}
