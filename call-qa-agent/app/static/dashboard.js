// Call QA pages, progressive enhancement: every page works without this script.
// - Filters apply as soon as a select changes (the Apply button is for pages without script).
// - The call page's Summary, Transcript and Checks sections become tabs; a link to something in another tab
//   (a quote, a timestamp, "Checks") opens that tab first.
// - Calls still being processed: the status badge follows the call's JSON (/calls/{id}) every 10 seconds, and the
//   page reloads once the call is done or failed. Without script a <noscript> meta refresh does the same.
(function () {
  "use strict";

  // ---- filters ----
  const filters = document.querySelector("form.filters");
  if (filters) {
    filters.querySelectorAll("[data-apply]").forEach((b) => (b.hidden = true));
    filters.addEventListener("change", (e) => {
      if (e.target.matches("select")) filters.requestSubmit ? filters.requestSubmit() : filters.submit();
    });
  }

  // ---- tabs ----
  const tablist = document.querySelector("[data-tabs]");
  if (tablist) {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const panels = tabs.map((t) => document.getElementById(t.getAttribute("aria-controls")));
    const select = (index, focus) => {
      tabs.forEach((tab, i) => {
        const on = i === index;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
        panels[i].hidden = !on;
      });
      document.body.dataset.tab = panels[index].id;
      if (focus) tabs[index].focus();
    };
    const panelOf = (el) => panels.indexOf(el && el.closest("[data-tab-panel]"));

    tablist.hidden = false;
    document.body.classList.add("has-tabs");
    panels.forEach((p, i) => {
      p.setAttribute("role", "tabpanel");
      p.setAttribute("aria-labelledby", tabs[i].id);
      p.tabIndex = 0;
    });
    const fromHash = location.hash ? panelOf(document.getElementById(location.hash.slice(1))) : -1;
    select(Math.max(fromHash, 0), false); // a link to a line or a check opens its tab, otherwise Summary

    tabs.forEach((tab, i) => tab.addEventListener("click", () => select(i, false)));
    tablist.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(document.activeElement);
      const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (i < 0 || to === undefined) return;
      e.preventDefault();
      select((to + tabs.length) % tabs.length, true);
    });
    // Before the browser follows an in-page link, show the tab its target is in.
    document.addEventListener("click", (e) => {
      const link = e.target.closest('a[href^="#"]');
      const i = link ? panelOf(document.getElementById(link.getAttribute("href").slice(1))) : -1;
      if (i > -1 && panels[i].hidden) select(i, false);
    }, true);
  }

  // ---- calls still being processed ----
  const WORKING = ["queued", "transcribing", "scoring"];
  const watched = [...document.querySelectorAll("[data-call-status]")].filter((el) => WORKING.includes(el.dataset.status));
  const ids = [...new Set(watched.map((el) => el.dataset.callStatus))];
  if (!ids.length || !window.fetch) return;

  async function check() {
    let finished = false;
    for (const id of ids) {
      try {
        const res = await fetch(`/calls/${encodeURIComponent(id)}`, { headers: { accept: "application/json" }, credentials: "same-origin" });
        if (!res.ok) continue;
        const status = String((await res.json()).status || "");
        if (!status) continue;
        document.querySelectorAll(`[data-call-status="${CSS.escape(id)}"]`).forEach((el) => {
          el.dataset.status = status;
          el.textContent = status;
          if (el.classList.contains("tag")) el.className = `tag status ${status}`;
        });
        if (!WORKING.includes(status)) finished = true;
      } catch {
        // offline for a moment: try again on the next round
      }
    }
    if (finished) location.reload(); // the server renders the score, the checks and the transcript
    else setTimeout(check, 10000);
  }
  setTimeout(check, 10000);
})();
