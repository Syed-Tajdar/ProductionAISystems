import { site } from "@/data/site";

export function Footer() {
  return (
    <footer className="mx-auto max-w-6xl px-5 sm:px-8">
      <div className="flex flex-col gap-2 border-t border-line py-8 text-sm text-muted sm:flex-row sm:justify-between">
        <p>
          &copy; {new Date().getFullYear()} {site.name}
        </p>
        <a href="#top" className="transition-colors hover:text-fg">
          Back to top
        </a>
      </div>
    </footer>
  );
}
