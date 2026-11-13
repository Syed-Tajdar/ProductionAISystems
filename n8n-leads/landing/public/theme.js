// Shared demo UI (demo-ui/ at the repository root; edit there, then run `python demo-ui/sync.py`).
// Loaded in <head> without defer, so the theme is set before the first paint and the page never flashes.
// The theme follows the system until the visitor picks one with the header toggle; the choice is kept in
// localStorage (every access in try/catch: without storage the toggle still works for this page view).
// Without this script the stylesheets follow the system theme through prefers-color-scheme.
(function () {
  var KEY = "theme";
  var root = document.documentElement;
  root.classList.add("du-js"); // lets the stylesheets tell, before the first paint, that scripts run
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function stored() {
    try {
      var t = window.localStorage.getItem(KEY);
      return t === "dark" || t === "light" ? t : null;
    } catch (e) {
      return null;
    }
  }
  function current() {
    return root.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }
  function label() {
    var dark = current() === "dark";
    document.querySelectorAll("[data-du-theme]").forEach(function (b) {
      b.setAttribute("aria-pressed", dark ? "true" : "false");
      b.title = dark ? "Switch to light theme" : "Switch to dark theme";
    });
  }
  function apply(theme) {
    root.setAttribute("data-theme", theme);
    label();
  }

  apply(stored() || (media && media.matches ? "dark" : "light"));
  if (media && media.addEventListener) {
    media.addEventListener("change", function (e) {
      if (!stored()) apply(e.matches ? "dark" : "light");
    });
  }
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-du-theme]").forEach(function (b) {
      b.hidden = false;
      b.addEventListener("click", function () {
        var next = current() === "dark" ? "light" : "dark";
        apply(next);
        try {
          window.localStorage.setItem(KEY, next);
        } catch (e) { /* kept for this page view only */ }
      });
    });
    label();
  });
})();
