"use client";

import { useState } from "react";

// Call QA cost calculator. Uses only the visitor's three inputs and the measured scoring cost per call; the defaults
// are examples. The page is rendered with the example values, so it reads correctly before (or without) JavaScript.

const COST_PER_CALL = 0.0102; // measured: average AI scoring cost per call in the eval, at list price
const DAYS = 30;
const EXAMPLE = { calls: 300, share: 5, minutes: 8 };

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: n < 100 ? 2 : 0, maximumFractionDigits: n < 100 ? 2 : 0 });
const num = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });

function Field({ id, label, value, min, max, step, suffix, onChange }: {
  id: string; label: string; value: number; min: number; max: number; step: number; suffix: string; onChange: (v: number) => void;
}) {
  return (
    <label htmlFor={id} className="grid gap-1.5">
      <span className="text-sm font-medium">{label}</span>
      <span className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode="decimal"
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(Math.min(max, Math.max(min, Number(e.target.value) || 0)))}
          className="h-10 w-28 rounded-lg border border-line bg-bg px-3 text-[15px] tabular-nums focus:border-accent focus:outline-none"
        />
        <span className="text-sm text-muted">{suffix}</span>
      </span>
    </label>
  );
}

export function CostCalculator() {
  const [calls, setCalls] = useState(EXAMPLE.calls);
  const [share, setShare] = useState(EXAMPLE.share);
  const [minutes, setMinutes] = useState(EXAMPLE.minutes);
  const isExample = calls === EXAMPLE.calls && share === EXAMPLE.share && minutes === EXAMPLE.minutes;

  const monthCalls = calls * DAYS;
  const aiCost = monthCalls * COST_PER_CALL;
  const allByHand = (monthCalls * minutes) / 60;
  const todayHours = allByHand * (share / 100);

  return (
    <section aria-labelledby="calculator-title" data-plain="calculator" className="mt-10 rounded-xl border border-line p-5 sm:p-6">
      <h2 id="calculator-title" className="text-lg font-semibold tracking-tight">What would checking every call cost your team?</h2>
      <p className="mt-1 text-sm text-muted">Enter your own numbers. The values filled in are examples, not measurements.</p>

      <div className="mt-5 grid gap-4 sm:grid-cols-3">
        <Field id="calc-calls" label="Calls per day" value={calls} min={1} max={100000} step={1} suffix="calls" onChange={setCalls} />
        <Field id="calc-share" label="Reviewed by hand today" value={share} min={0} max={100} step={0.5} suffix="% of calls" onChange={setShare} />
        <Field id="calc-minutes" label="Time per manual review" value={minutes} min={0} max={240} step={0.5} suffix="minutes" onChange={setMinutes} />
      </div>

      <dl aria-live="polite" className="mt-6 grid gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3">
        <div className="bg-bg px-4 py-3">
          <dt className="text-[13px] text-muted">Calls checked</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">
            {num(share)}% today <span className="text-accent">&rarr; 100% with AI</span>
          </dd>
        </div>
        <div className="bg-bg px-4 py-3">
          <dt className="text-[13px] text-muted">AI scoring cost for every call</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums text-accent">{usd(aiCost)} a month</dd>
        </div>
        <div className="bg-bg px-4 py-3">
          <dt className="text-[13px] text-muted">Reviewer time to check every call by hand</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums">{num(allByHand)} hours a month</dd>
          <dd className="text-[13px] text-muted">today: {num(todayHours)} hours for {num(share)}% of calls</dd>
        </div>
      </dl>
      {isExample && <p className="mt-2 text-[13px] text-muted">Shown with the example values.</p>}

      <div className="mt-5 text-[13px] leading-relaxed text-muted">
        <p className="font-medium text-fg">How it is worked out</p>
        <ul className="mt-1.5 grid gap-1">
          <li>Calls a month = calls per day &times; {DAYS}</li>
          <li>AI scoring cost = calls a month &times; $0.0102, the measured average scoring cost per call in the tests at list price. Speech-to-text is charged separately by the transcription provider per minute of audio and is not included.</li>
          <li>Hours to check every call by hand = calls a month &times; minutes per review &divide; 60</li>
          <li>Hours today = that &times; the share you review by hand now</li>
        </ul>
        <p className="mt-2">AI flags anything it can&apos;t back with a quote for a person to check, so some review time remains.</p>
      </div>
    </section>
  );
}
