"""The graph panel's markup and CSS. Substituted into HTML_TEMPLATE as format VALUES:
braces NOT doubled, never .format() them."""

from .layout import LAYOUT_CONTROL


GRAPH_CSS = """
  .viewswitch { display: inline-flex; border: 1px solid var(--border); border-radius: 8px;
         overflow: hidden; background: var(--card); }
  .viewswitch button { border: 0; background: transparent; color: var(--text-dim);
         padding: 8px 15px; font-size: 13px; cursor: pointer; }
  .viewswitch button.on { background: var(--link); color: #fff; }
  .graph-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 11px;
         box-shadow: 0 1px 3px rgba(16,24,40,0.06); overflow: hidden; }
  .graph-controls { display: flex; flex-wrap: wrap; gap: 14px; align-items: center;
         padding: 11px 15px; border-bottom: 1px solid var(--border);
         background: var(--card-2); font-size: 12.5px; color: var(--text-dim); }
  .graph-controls label.chk { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; }
  .graph-controls select { padding: 5px 8px; border-radius: 7px; border: 1px solid var(--border);
         background: var(--card); color: var(--text); font-size: 12.5px; }
  .graph-controls .counts { margin-left: auto; font-variant-numeric: tabular-nums; }
  #graph { height: 84vh; background: var(--card); position: relative; }
  /* Fullscreen is a fixed overlay with controls on top. Explicit vw/vh and min-height:0
     stop a canvas-sized flex child from growing past the screen. */
  .graph-wrap.fullscreen { position: fixed; inset: 0; top: 0; right: 0; bottom: 0; left: 0;
         width: 100vw; height: 100vh; z-index: 1000; border-radius: 0;
         display: flex; flex-direction: column; }
  .graph-wrap.fullscreen #graph { height: auto; flex: 1 1 auto; min-height: 0; }
  .graph-wrap.fullscreen .graph-inspector { flex: 0 0 auto; }
  .graph-wrap.fullscreen .graph-legend, .graph-wrap.fullscreen .graph-color-legend { flex: 0 0 auto; }
  .graph-fullscreen-btn { border: 1px solid var(--border); border-radius: 7px;
         background: var(--card); color: var(--text-dim); padding: 5px 10px;
         font-size: 12.5px; cursor: pointer; }
  /* A second copy of the colour legend, shown only in fullscreen (which covers the page's). */
  .graph-color-legend { display: none; padding: 10px 15px 0 15px; margin-bottom: 0; }
  .graph-wrap.fullscreen .graph-color-legend { display: flex; }
  /* The tooltip carries the full name that truncated labels cannot. */
  .graph-tip { position: absolute; display: none; z-index: 5; pointer-events: none;
         max-width: 340px; background: #1f2933; color: #f5f7fa; border-radius: 7px;
         padding: 7px 10px; font-size: 12px; line-height: 1.45;
         box-shadow: 0 3px 10px rgba(16,24,40,0.28); word-break: break-all; }
  .graph-tip b { font-weight: 600; }
  .graph-tip .tip-sub { color: #b8c2cc; font-size: 11.5px; word-break: normal; }
  .graph-note { padding: 22px 18px; font-size: 13px; color: var(--text-dim); line-height: 1.6; }
  .graph-inspector { border-top: 1px solid var(--border); background: var(--card-2);
         padding: 12px 15px; font-size: 12.5px; line-height: 1.6; max-height: 210px; overflow: auto; }
  .graph-inspector .ins-title { font-weight: 600; color: var(--text); font-size: 13px; }
  .graph-inspector dl { display: grid; grid-template-columns: max-content 1fr; gap: 3px 12px; margin: 8px 0 0 0; }
  .graph-inspector dt { color: var(--text-dim); }
  .graph-inspector dd { margin: 0; color: var(--text); word-break: break-word; }
  .focus-chip { display: none; align-items: center; gap: 8px; background: #eef5ff;
         border: 1px solid #c3dbf7; color: #1b4f8a; border-radius: 8px;
         padding: 7px 12px; font-size: 12.5px; margin-bottom: 14px; }
  .focus-chip button { border: 0; background: transparent; color: #1b4f8a;
         cursor: pointer; font-size: 15px; line-height: 1; padding: 0 2px; }
  .graph-legend { display: none; flex-wrap: wrap; gap: 16px; margin-bottom: 16px;
         font-size: 11.5px; color: var(--text-dim); align-items: center; }
  .graph-legend .ln { display: inline-block; width: 26px; height: 0; vertical-align: middle;
         margin-right: 6px; border-top-width: 2px; }
  .graph-legend .ln-solid { border-top: 2px solid #5b6b7c; }
  .graph-legend .ln-dashed { border-top: 2px dashed #b07d2b; }
  .graph-legend .ln-attr { border-top: 2px dotted #9aa7b4; }
  .graph-legend .ln-bridge { border-top: 2px solid #7b93b8; opacity: 0.75; }
  .graph-legend .ghost { display: inline-block; width: 11px; height: 11px; border-radius: 50%;
         border: 1.5px dashed #9aa7b4; margin-right: 6px; vertical-align: middle; }
  .graph-legend .boxi { display: inline-block; width: 13px; height: 10px; border: 1.5px dashed #8fa3b8;
         background: rgba(143,163,184,0.12); margin-right: 6px; vertical-align: middle; }
  .graph-legend .boxi-tier { border: 1px dotted #8b7ab8; background: rgba(139,122,184,0.10); }
"""

GRAPH_SWITCH = """
    <div>
      <label>View</label>
      <div class="viewswitch" id="viewSwitch">
        <button type="button" data-view="table" class="on">Table</button><button type="button" data-view="graph">Graph</button><button type="button" data-view="diagram">Diagram</button>
      </div>
    </div>
"""

# ABOVE the table, so a filter made in the graph view stays visible in Table view.
GRAPH_PANEL = """
  <div class="focus-chip" id="focusChip">
    <span id="focusChipText"></span>
    <button type="button" id="focusChipClear" title="Clear graph focus">&times;</button>
  </div>

  <div class="graph-wrap" id="graphWrap" style="display:none">
    <div class="legend graph-color-legend" id="graphColorLegend"></div>
    <div class="graph-legend" id="graphLegend">
      <span><i class="ln ln-solid"></i>link, used for project grouping</span>
      <span><i class="ln ln-dashed"></i>report-only: shown but not used for project grouping</span>
      <span><i class="ln ln-attr"></i>grouped by a shared attribute, not by a link</span>
      <span><i class="ln ln-bridge"></i>inferred through a hidden supporting resource</span>
      <span><i class="ghost"></i>target not found in this scan</span>
      <span><i class="boxi"></i>project group</span>
      <span><i class="boxi boxi-tier"></i>runtime / deployment, inside one project</span>
    </div>
    <div class="graph-controls">
      <div>Scope <select id="graphScope"></select></div>
""" + LAYOUT_CONTROL + """
      <label class="chk"><input type="checkbox" id="graphHideIsolated" checked> Hide unconnected</label>
      <label class="chk" id="graphHideHubsLabel"><input type="checkbox" id="graphHideHubs" checked> Hide shared plumbing hubs</label>
      <label class="chk" id="graphHideDeploymentLabel"><input type="checkbox" id="graphHideDeployment"> Hide deployment-only resources</label>
      <label class="chk"><input type="checkbox" id="graphShowReportOnly" checked> Report-only links</label>
      <label class="chk"><input type="checkbox" id="graphShowAttr" checked> Shared-attribute links</label>
      <span class="counts" id="graphCounts"></span>
      <button type="button" class="graph-fullscreen-btn" id="graphFullscreenBtn">Fullscreen</button>
    </div>
    <div id="graph"><div class="graph-tip" id="graphTip"></div></div>
    <div class="graph-inspector" id="graphInspector">
      Hover a node or link for its full name. Click a node to see what it is, why it was grouped,
      and to narrow the canvas and the table to it and its neighbours.
      Click a link to see what it was inferred from.
    </div>
  </div>
"""
