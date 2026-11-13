import { isSet } from "@/data/site";

type Props = { href?: string; children: React.ReactNode; className?: string; rel?: string };

// Renders a real link only when the href is filled in; placeholders stay plain text.
// External links open in a new tab; rel defaults to "noopener noreferrer".
export function TextLink({ href, children, className = "", rel = "noopener noreferrer" }: Props) {
  if (!isSet(href)) {
    return (
      <span className={`cursor-default text-muted ${className}`} title="Link not added yet">
        {children}
      </span>
    );
  }
  const external = /^https?:/.test(href);
  return (
    <a
      href={href}
      className={`text-accent underline-offset-4 transition-colors hover:underline ${className}`}
      {...(external ? { target: "_blank", rel } : {})}
    >
      {children}
    </a>
  );
}
