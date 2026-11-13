// Call page audio: highlights the transcript line being played, and a tap on a quote, a rubric item or a
// timestamp plays from that line. The page works without this script (links still jump to the line).
(function () {
  const audio = document.querySelector("audio[data-call-audio]");
  if (!audio) return;
  const lines = [...document.querySelectorAll(".transcript .line[data-start]")];
  const starts = lines.map((l) => parseFloat(l.dataset.start));
  let current = null;

  function mark() {
    const t = audio.currentTime;
    let i = -1;
    while (i + 1 < starts.length && starts[i + 1] <= t + 0.05) i++;
    const line = i >= 0 ? lines[i] : null;
    if (line === current) return;
    if (current) { current.classList.remove("playing"); current.removeAttribute("aria-current"); }
    current = line;
    if (line) { line.classList.add("playing"); line.setAttribute("aria-current", "true"); }
  }
  ["timeupdate", "seeked"].forEach((ev) => audio.addEventListener(ev, mark));

  function playFrom(seconds) {
    const go = () => { audio.currentTime = seconds; mark(); audio.play().catch(() => {}); };
    if (audio.readyState >= 1) go(); else { audio.addEventListener("loadedmetadata", go, { once: true }); audio.load(); }
  }
  document.addEventListener("click", (e) => {
    const link = e.target.closest('a[href^="#u-"]');
    const line = link && document.getElementById(link.getAttribute("href").slice(1));
    if (line && line.dataset.start) playFrom(parseFloat(line.dataset.start)); // the link still scrolls to the line
  });

  // The signed audio URL is short-lived. When it has expired, fetch a fresh one and continue from the same point.
  let lastTime = 0, wasPlaying = false, retries = 0;
  audio.addEventListener("timeupdate", () => { lastTime = audio.currentTime; });
  audio.addEventListener("play", () => { wasPlaying = true; });
  audio.addEventListener("pause", () => { if (!audio.error) wasPlaying = false; });
  audio.addEventListener("playing", () => { retries = 0; });
  audio.addEventListener("error", () => {
    if (retries++ >= 2) return;
    const resume = wasPlaying;
    audio.src = audio.dataset.src + "?fresh=" + Date.now();
    audio.addEventListener("loadedmetadata", () => {
      audio.currentTime = lastTime;
      if (resume) audio.play().catch(() => {});
    }, { once: true });
  });
})();
