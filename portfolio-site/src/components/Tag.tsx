export function Tag({ children }: { children: React.ReactNode }) {
  return (
    <li className="rounded-md border border-line px-2 py-0.5 font-mono text-[11.5px] text-muted">
      {children}
    </li>
  );
}
