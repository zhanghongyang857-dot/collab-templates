// NOW 页增强：任务链接旁加「看板」入口；file:// 链接交给本机服务用默认应用打开；定时整页刷新拿最新内容。
(function () {
  let byProgress = {};

  function openFile(href) {
    const path = decodeURIComponent(href.replace(/^file:\/\//, ""));
    fetch("/api/open", {method: "POST", headers: {"Content-Type": "application/json", "X-Board": "1"},
      body: JSON.stringify({path})});
  }

  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href^="file://"]');
    if (!a) return;
    e.preventDefault();
    openFile(a.getAttribute("href"));
  });

  function addChips() {
    document.querySelectorAll('a[href^="file://"]').forEach((a) => {
      if (a.dataset.bd) return;
      a.dataset.bd = "1";
      const path = decodeURIComponent(a.getAttribute("href").replace(/^file:\/\//, ""));
      const p = byProgress[path];
      if (!p) return;
      const chip = document.createElement("a");
      chip.className = "bd-chip";
      chip.href = "/p/" + p.id;
      chip.textContent = p.summary ? "看板 · " + p.summary : "看板";
      if (/受阻/.test(p.summary || "")) chip.classList.add("bd-warn");
      a.after(chip);
    });
  }

  fetch("/api/projects").then((r) => r.json()).then((list) => {
    list.forEach((p) => { byProgress[p.progress] = p; });
    addChips();
    new MutationObserver(addChips).observe(document.body, {childList: true, subtree: true});
  });

  const live = document.createElement("div");
  live.className = "bd-live";
  live.textContent = "本地服务 · 回到此页时自动刷新";
  document.body.appendChild(live);
  const loaded = Date.now();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - loaded > 30000) location.reload();
  });
})();
