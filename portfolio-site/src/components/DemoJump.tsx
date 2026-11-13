"use client";

import { useEffect, useState } from "react";

// Phones only: a small "Try a demo" button once the hero has scrolled away. Hidden while the projects are on screen
// (it would point at what is already visible) and over the recruiter and contact links, which carry their own demo links.
// While hidden it is out of the tab order and the accessibility tree; without JavaScript it never shows.
export function DemoJump() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const ids = ["top", "projects", "recruiters", "contact"];
    const inView: Record<string, boolean> = { top: true, projects: false, recruiters: false, contact: false };
    const observer = new IntersectionObserver((entries) => {
      for (const e of entries) inView[e.target.id] = e.isIntersecting;
      setShow(!inView.top && !inView.projects && !inView.recruiters && !inView.contact);
    });
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);

  return (
    <a
      href="#projects"
      hidden={!show}
      className="fixed bottom-[calc(1rem+env(safe-area-inset-bottom))] right-4 z-30 inline-flex h-10 items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-medium text-accent-fg shadow-[0_8px_24px_-8px_rgb(0_0_0/0.45)] motion-safe:animate-[fadein_150ms_ease-out] md:!hidden"
    >
      Try a demo <span aria-hidden="true">&darr;</span>
    </a>
  );
}
