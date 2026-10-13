"""The report's Table / Diagram switch: markup, CSS and the JS that flips views.
Format VALUES substituted into HTML_TEMPLATE: braces NOT doubled, never .format() them."""


VIEW_CSS = """
  .viewswitch { display: inline-flex; border: 1px solid var(--border); border-radius: 8px;
         overflow: hidden; background: var(--card); }
  .viewswitch button { border: 0; background: transparent; color: var(--text-dim);
         padding: 8px 15px; font-size: 13px; cursor: pointer; }
  .viewswitch button.on { background: var(--link); color: #fff; }
  .panel-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 11px;
         box-shadow: 0 1px 3px rgba(16,24,40,0.06); overflow: hidden; }
  .panel-controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
         padding: 11px 15px; border-bottom: 1px solid var(--border);
         background: var(--card-2); font-size: 12.5px; color: var(--text-dim); }
  .panel-controls select { padding: 5px 8px; border-radius: 7px; border: 1px solid var(--border);
         background: var(--card); color: var(--text); font-size: 12.5px; }
"""

VIEW_SWITCH = """
    <div>
      <label>View</label>
      <div class="viewswitch" id="viewSwitch">
        <button type="button" data-view="table" class="on">Table</button><button type="button" data-view="diagram">Diagram</button>
      </div>
    </div>
"""

# After DIAGRAM_JS: showView calls its initDiagram().
VIEW_JS = r"""
(function () {
  const tableWrap = document.querySelector(".table-wrap");
  const diagramWrap = document.getElementById("diagramWrap");
  function showView(which) {
    const diagramMode = which === "diagram";
    if (diagramWrap) diagramWrap.style.display = diagramMode ? "" : "none";
    tableWrap.style.display = diagramMode ? "none" : "";
    document.querySelectorAll("#viewSwitch button").forEach(b =>
      b.classList.toggle("on", b.dataset.view === which));
    if (diagramMode) initDiagram();
  }
  document.querySelectorAll("#viewSwitch button").forEach(b =>
    b.addEventListener("click", () => showView(b.dataset.view)));
})();
"""
