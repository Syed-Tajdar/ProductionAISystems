import Link from "next/link";
import { site } from "@/data/site";
import { ThemeToggle } from "./ThemeToggle";

// Header for the case study and project pages.
export function PageHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-line/70 bg-bg/80 backdrop-blur-md print:hidden">
      <nav aria-label="Main" className="mx-auto flex h-14 max-w-6xl items-center justify-between px-5 sm:px-8">
        <Link href="/" className="flex items-center gap-2.5 text-sm font-semibold tracking-tight">
          <span className="size-2 rounded-full bg-accent" aria-hidden="true" />
          {site.shortName}
        </Link>
        <div className="flex items-center gap-1 sm:gap-2">
          <Link href="/#projects" className="rounded-md px-3 py-2 text-sm text-muted transition-colors hover:text-fg">
            All projects
          </Link>
          <ThemeToggle />
        </div>
      </nav>
    </header>
  );
}
