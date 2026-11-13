import { site } from "@/data/site";
import { ThemeToggle } from "./ThemeToggle";

const links = [
  { href: "#services", label: "Services" },
  { href: "#projects", label: "Projects" },
  { href: "#experience", label: "Experience" },
  { href: "#skills", label: "Skills" },
  { href: "#recruiters", label: "For recruiters" },
  { href: "#automations", label: "Automations" },
  { href: "#contact", label: "Contact" },
];

export function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/80 backdrop-blur-md">
      <nav aria-label="Main" className="mx-auto flex h-14 max-w-6xl items-center justify-between px-5 sm:px-8">
        <a href="#top" className="flex items-center gap-2.5 text-sm font-semibold tracking-tight">
          <span className="size-2 rounded-full bg-accent" aria-hidden="true" />
          {site.shortName}
        </a>
        <div className="flex items-center gap-1 sm:gap-2">
          <ul className="hidden items-center md:flex">
            {links.map((l) => (
              <li key={l.href}>
                <a href={l.href} className="rounded-md px-2.5 py-2 text-sm text-muted transition-colors hover:text-fg lg:px-3">
                  {l.label}
                </a>
              </li>
            ))}
          </ul>
          <a href="#projects" className="rounded-md px-2 py-2 text-sm font-medium text-accent transition-colors hover:text-fg md:hidden">
            Demos
          </a>
          <a href="#contact" className="rounded-md px-2 py-2 text-sm text-muted transition-colors hover:text-fg md:hidden">
            Contact
          </a>
          <ThemeToggle />
        </div>
      </nav>
    </header>
  );
}
