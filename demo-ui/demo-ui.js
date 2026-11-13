// Shared demo UI (demo-ui/ at the repository root; edit there, then run `python demo-ui/sync.py`).
// Loaded right after each "what this demo shows" card: hides the card if this browser dismissed it before,
// and shows its close button. Storage can throw (private mode, blocked site data), so every access is guarded
// and the card simply stays visible when storage is unavailable.
(function () {
  const key = (id) => "demo-ui:dismissed:" + id;
  const read = (id) => {
    try { return window.localStorage.getItem(key(id)) === "1"; } catch { return false; }
  };
  const write = (id) => {
    try { window.localStorage.setItem(key(id), "1"); } catch { /* stays dismissed until reload */ }
  };
  // Folds a collapsible card to its title line (the visitor can open it again).
  window.duFoldCard = (id) => {
    const fold = document.querySelector(`[data-du-card="${id}"] .du-card-fold`);
    if (fold) fold.open = false;
  };
  document.querySelectorAll("[data-du-card]:not([data-du-ready])").forEach((card) => {
    card.dataset.duReady = "";
    const id = card.dataset.duCard;
    if (read(id)) { card.hidden = true; return; }
    const button = card.querySelector("[data-du-dismiss]");
    if (!button) return;
    button.hidden = false;
    button.addEventListener("click", () => { card.hidden = true; write(id); });
  });
})();
