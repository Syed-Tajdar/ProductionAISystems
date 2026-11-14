import { aiLines, aiTotal, cardAiTotal, cardGapCents, controls, EXAMPLE_VOLUME, freeTiers, hosting, hostingTotal, measuredRows, money, monthTotal } from "@/data/costs";
import { codeLink } from "@/data/site";
import { Section } from "./Section";

function Source({ path, field }: { path: string; field: string }) {
  const href = codeLink(path);
  const name = <code className="break-all font-mono text-[12px]">{path}</code>;
  return (
    <p className="mt-2 text-[12px] leading-relaxed text-muted">
      Source: {href ? <a href={href} className="underline decoration-line underline-offset-2 hover:text-fg">{name}</a> : name} ({field})
    </p>
  );
}

const h3 = "text-sm font-semibold tracking-tight";

// One line of the estimate. On a phone the arithmetic sits under the item name instead of in its own column.
function Row({ name, how, amount }: { name: string; how: string; amount: string }) {
  return (
    <tr>
      <th scope="row" className="px-4 py-2.5 align-top font-medium">
        {name}
        <span className="mt-0.5 block text-[13px] font-normal tabular-nums text-muted sm:hidden">{how}</span>
      </th>
      <td className="hidden px-4 py-2.5 align-top font-normal tabular-nums text-muted sm:table-cell">{how}</td>
      <td className="px-4 py-2.5 text-right align-top tabular-nums">{amount}</td>
    </tr>
  );
}

export function RunningCost() {
  return (
    <Section id="running-cost" index="08" title="What it costs to run" intro="The AI cost of each piece of work, measured in the tests, and what hosting costs today and would cost in production.">
      <h3 className={h3}>
        Measured: AI cost per item <span className="font-normal text-muted">(list prices, averages from the test runs)</span>
      </h3>
      <ul className="mt-3 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3">
        {measuredRows.map((m) => (
          <li key={m.id} className="bg-bg p-5">
            <div data-plain="running-cost">
              <p className="text-[13px] text-muted">{m.item}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{m.average}</p>
              <p className="mt-1 text-[14px] leading-relaxed">
                {m.plain}, or {m.perDollar} for one dollar
              </p>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-muted">
              Exact average ${m.mean}, from {m.range} per item. {m.scope}
            </p>
            <Source path={m.source} field={m.field} />
          </li>
        ))}
      </ul>

      <h3 className={`${h3} mt-10`}>Hosting today: $0 a month</h3>
      <ul data-plain="running-cost" className="mt-3 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
        {freeTiers.map((t) => (
          <li key={t.name} className="bg-bg p-5">
            <p className="font-medium">
              {t.name} <span className="font-normal text-muted">for {t.runs}</span>
            </p>
            <p className="mt-1 text-[14px] leading-relaxed text-muted">{t.limit}</p>
          </li>
        ))}
      </ul>

      <div className="mt-10 flex flex-wrap items-center gap-2">
        <h3 className={h3}>In production, for a client</h3>
        <span className="rounded-full border border-line px-2 py-0.5 text-[12px] font-medium text-muted">Estimate, not measured</span>
      </div>
      <div data-plain="running-cost" className="mt-3 overflow-hidden rounded-xl border border-line">
        <table className="w-full text-left text-[14px]">
          <caption className="sr-only">Estimated monthly cost in production</caption>
          <thead className="border-b border-line text-[13px] text-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Monthly item</th>
              <th scope="col" className="hidden px-4 py-2 font-medium sm:table-cell">How it is worked out</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">A month</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {hosting.map((h) => (
              <Row key={h.name} name={h.name} how={`${h.why}; list price`} amount={money(h.monthly)} />
            ))}
            {aiLines.map((l) => (
              <Row key={l.id} name={`AI for ${l.label}`} how={`${l.sum} measured average`} amount={money(l.total)} />
            ))}
          </tbody>
          <tfoot className="border-t border-line font-semibold">
            <Row name="Total" how={`${money(hostingTotal)} hosting + ${money(aiTotal)} AI`} amount={money(monthTotal)} />
          </tfoot>
        </table>
      </div>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">
        An example of {EXAMPLE_VOLUME.toLocaleString("en-US")} calls, {EXAMPLE_VOLUME.toLocaleString("en-US")} questions and{" "}
        {EXAMPLE_VOLUME.toLocaleString("en-US")} enquiries a month, at list prices checked on 14 November 2026. Speech-to-text for the calls is billed
        per audio minute and is not included, because it was not measured.
      </p>
      <p className="mt-1 text-[13px] leading-relaxed text-muted">
        The AI lines use the exact averages shown on the cards above. The large card figures are cut, never rounded up,
        so 1,000 times each of them gives {money(cardAiTotal)}, {cardGapCents} cents less than {money(aiTotal)}.
      </p>

      <h3 className={`${h3} mt-10`}>Cost controls already built in</h3>
      <ul data-plain="running-cost" className="mt-3 grid gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2">
        {controls.map((c) => (
          <li key={c.title} className="bg-bg p-5 sm:odd:last:col-span-2">
            <p className="font-medium">{c.title}</p>
            <p className="mt-1 text-[14px] leading-relaxed text-muted">{c.body}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
