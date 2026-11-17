"use client";

import { useRef, useState } from "react";
import { demoVideo } from "@/data/demo-video";

const DEMO_VIDEO = demoVideo.mp4;
const POSTER = demoVideo.poster;

// A link to the MP4 that opens it in a modal dialog instead. Without JavaScript, or with a modifier key, the link
// is followed. Nothing of the video is fetched until the dialog first opens: the <video> is only rendered then,
// with preload="none", so the poster is the first thing it loads.
export function WatchDemo({ className, children }: { className: string; children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLAnchorElement>(null);
  const closer = useRef<HTMLButtonElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [mounted, setMounted] = useState(false);

  const open = (e: React.MouseEvent) => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (typeof dialog.current?.showModal !== "function") return;
    e.preventDefault();
    setMounted(true);
    dialog.current.showModal();
    closer.current?.focus();
  };

  const onClose = () => {
    video.current?.pause();
    opener.current?.focus();
  };

  // A click on the backdrop lands on the dialog element itself; clicks on its content land on a child.
  const onClick = (e: React.MouseEvent<HTMLDialogElement>) => {
    if (e.target === e.currentTarget) e.currentTarget.close();
  };

  // The rest of the page is inert while the dialog is modal; this keeps Tab cycling inside it as well.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDialogElement>) => {
    if (e.key !== "Tab") return;
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>("button, video")];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      <a ref={opener} href={DEMO_VIDEO} onClick={open} aria-haspopup="dialog" className={className}>
        {children}
      </a>
      <dialog
        ref={dialog}
        aria-labelledby="demo-video-title"
        onClose={onClose}
        onClick={onClick}
        onKeyDown={onKeyDown}
        className="m-auto w-[min(94vw,1120px)] max-w-none overflow-visible rounded-xl border border-line bg-bg p-0 text-fg backdrop:bg-black/75"
      >
        <div className="p-3 sm:p-4">
          <div className="mb-3 flex items-center justify-between gap-4">
            <h2 id="demo-video-title" className="text-[15px] font-semibold tracking-tight sm:text-base">
              1-minute demo <span className="font-normal text-muted">(silent, with captions)</span>
            </h2>
            <button
              ref={closer}
              type="button"
              onClick={() => dialog.current?.close()}
              aria-label="Close the demo video"
              className="grid size-10 shrink-0 place-items-center rounded-md border border-line text-muted transition-colors hover:border-accent hover:text-fg"
            >
              <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6L6 18" />
              </svg>
            </button>
          </div>
          {mounted && (
            <video
              ref={video}
              controls
              muted
              playsInline
              preload="none"
              poster={POSTER}
              width={1920}
              height={1080}
              aria-label="Demo: the portfolio, a call review with its quoted evidence, cited answers in English and Arabic, and a lead sorted with its sales alert"
              className="block aspect-video h-auto w-full rounded-lg bg-black"
            >
              <source src={DEMO_VIDEO} type="video/mp4" />
              <a href={DEMO_VIDEO}>Download the demo video (MP4)</a>
            </video>
          )}
        </div>
      </dialog>
    </>
  );
}
