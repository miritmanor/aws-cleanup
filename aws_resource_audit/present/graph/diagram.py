"""The Mermaid diagram tab: ships the same string as the .mmd file, HTML-escaped
(mermaid reads textContent). Format VALUES: braces NOT doubled, never .format() them."""

import html
import json

from .icons import ICON_PACK

# UMD build so it works from a file:// URL; 11.3+ for icon nodes (the web app pins the same major).
MERMAID_VERSION = "11.17.2"

DIAGRAM_HEAD = (
    f'<script src="https://unpkg.com/mermaid@{MERMAID_VERSION}'
    '/dist/mermaid.min.js"></script>'
)

DIAGRAM_CSS = """
  /* Wide diagrams scroll both ways rather than shrinking until unreadable. */
  .diagram-scroll { overflow: auto; max-height: 72vh; padding: 18px; background: var(--card); }
  .diagram-scroll .mermaid { display: block; }
  .diagram-scroll svg { max-width: none !important; height: auto; }
  .diagram-note { padding: 22px 18px; font-size: 13px; color: var(--text-dim); line-height: 1.6; }
"""

_PANEL_OPEN = """
  <div class="graph-wrap" id="diagramWrap" style="display:none">
    <div class="graph-controls">
      <span>The application&rsquo;s runtime shape: solid lines are calls and data use; dotted
        lines are likely but unproven (IAM access, a name match). Use the Graph view for every link.</span>
      <span id="diagramScopeWrap" style="display:none">
        <label for="diagramScope">Scope</label>
        <select id="diagramScope"></select>
      </span>
      <a id="diagramDownload" class="linkish" download>Download this .mmd</a>
      <a id="diagramDrawioDownload" class="linkish" download>Download this .drawio</a>
    </div>
    <div class="diagram-scroll" id="diagramScroll"><pre class="mermaid" id="diagramSrc">"""

_PANEL_CLOSE = """</pre></div>
  </div>
"""

# The icon pack, inline: "</" is escaped so no SVG body can close the script tag.
_ICONS_SCRIPT = ("<script>const ARCH_ICONS = "
                 + json.dumps(ICON_PACK).replace("</", "<\\/") + ";</script>\n")


def diagram_panel(source, projects=()):
    """The panel markup around one rendered Mermaid document, joined by concatenation
    since the source is full of brackets and quotes."""
    del projects     # the <option> list is built in JS from DIAGRAM_SOURCES
    return _ICONS_SCRIPT + _PANEL_OPEN + html.escape(source) + _PANEL_CLOSE


DIAGRAM_JS = r"""
// --- Mermaid diagram view. initialize() runs eagerly here, before mermaid's own
// DOMContentLoaded listener can lay out the still-hidden source with NaN positions.
if (typeof mermaid !== "undefined") {
  mermaid.initialize({
    startOnLoad: false,
    theme: "neutral",
    // A real account's contracted graph runs well past the 50k default, and
    // the failure is a bare "maximum text size exceeded" with no diagram.
    maxTextSize: 500000,
    flowchart: {useMaxWidth: false, htmlLabels: true, curve: "basis"},
  });
  if (typeof ARCH_ICONS !== "undefined" && mermaid.registerIconPacks) {
    mermaid.registerIconPacks([{name: ARCH_ICONS.prefix, icons: ARCH_ICONS}]);
  }
}

let diagramRendered = false;

// The scope drawn: "" is the account, else a project id in DIAGRAM_SOURCES (rendered in
// Python; this only picks a finished string).
let diagramScope = "";
let diagramSeq = 0;

function diagramNote(scroll, message) {
  scroll.innerHTML = '<div class="diagram-note">' + message + "</div>";
}

// mermaid.render(), not run(): run() marks its target processed and never redraws it.
function drawDiagram() {
  const scroll = document.getElementById("diagramScroll");
  const sources = (typeof DIAGRAM_SOURCES === "undefined") ? null : DIAGRAM_SOURCES;
  if (typeof mermaid === "undefined") {
    // Only this view needs the CDN; the table, the project overview and the
    // embedded data are all fine without it.
    diagramNote(scroll,
      "The diagram view needs the Mermaid library, which is loaded from " +
      "unpkg.com and could not be fetched. The table and the Projects panel " +
      "work offline, and the same diagram is written beside this file as a " +
      ".mmd you can paste anywhere that renders Mermaid.");
    return;
  }
  const source = sources && Object.prototype.hasOwnProperty.call(sources, diagramScope)
    ? sources[diagramScope]
    : (sources ? sources[""] : "");
  if (!source) {
    diagramNote(scroll, "This project has no diagram - nothing it owns is " +
                        "connected to anything else in the scan.");
    return;
  }
  diagramSeq += 1;
  mermaid.render("diagram-svg-" + diagramSeq, source)
    .then(function (result) { scroll.innerHTML = result.svg; })
    .catch(function (err) {
      diagramNote(scroll,
        "This diagram could not be rendered: " +
        String(err && err.message ? err.message : err) +
        ". The same graph is in the Graph view, and beside this file as a .mmd.");
    });
}

function initDiagram() {
  if (diagramRendered) return;
  diagramRendered = true;

  const sources = (typeof DIAGRAM_SOURCES === "undefined") ? null : DIAGRAM_SOURCES;
  const select = document.getElementById("diagramScope");
  const wrap = document.getElementById("diagramScopeWrap");
  const names = (typeof DIAGRAM_PROJECT_NAMES === "undefined") ? {} : DIAGRAM_PROJECT_NAMES;
  const ids = sources ? Object.keys(sources).filter(function (k) { return k !== ""; }) : [];
  // One project is no choice: its diagram and the account's are the same
  // picture, and a selector with a single option is furniture.
  if (select && wrap && ids.length > 1) {
    const options = ['<option value="">Whole account</option>'].concat(
      ids.map(function (id) {
        return '<option value="' + id.replace(/"/g, "&quot;") + '">' +
               String(names[id] || id).replace(/[&<>]/g, function (c) {
                 return {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c];
               }) + "</option>";
      }));
    select.innerHTML = options.join("");
    select.value = diagramScope;
    select.addEventListener("change", function () {
      diagramScope = select.value;
      updateDiagramDownload();
      drawDiagram();
    });
    wrap.style.display = "";
  }
  updateDiagramDownload();
  drawDiagram();
}

// Download link for the scope shown, using the same file naming rule as Python.
function updateDiagramDownload() {
  setDiagramLink("diagramDownload", typeof DIAGRAM_FILES === "undefined" ? {} : DIAGRAM_FILES);
  setDiagramLink("diagramDrawioDownload",
                 typeof DIAGRAM_DRAWIO_FILES === "undefined" ? {} : DIAGRAM_DRAWIO_FILES);
}

function setDiagramLink(id, files) {
  const link = document.getElementById(id);
  if (!link) return;
  const file = files[diagramScope] || files[""];
  if (!file) { link.style.display = "none"; return; }
  link.style.display = "";
  link.href = file;
  link.textContent = "Download " + file;
}
"""
