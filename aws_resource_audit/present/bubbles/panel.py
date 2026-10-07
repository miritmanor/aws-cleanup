"""The overview panel in the HTML report: CSS and markup. Substituted as format VALUES:
braces are NOT doubled and .format() must never be called on these strings."""


BUBBLES_CSS = """
  .bubbles-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 11px;
         box-shadow: 0 1px 3px rgba(16,24,40,0.06); margin-bottom: 18px; overflow: hidden; }
  .bubbles-head { display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
         padding: 11px 15px; border-bottom: 1px solid var(--border);
         background: var(--card-2); font-size: 12.5px; color: var(--text-dim); }
  .bubbles-head .metricswitch { display: inline-flex; border: 1px solid var(--border);
         border-radius: 8px; overflow: hidden; background: var(--card); }
  .bubbles-head .metricswitch button { border: 0; background: transparent; color: var(--text-dim);
         padding: 7px 13px; font-size: 12.5px; cursor: pointer; }
  .bubbles-head .metricswitch button.on { background: var(--link); color: #fff; }
  .bubbles-head .metricswitch button:disabled { opacity: 0.45; cursor: not-allowed; }
  .bubbles-head .bubbles-title { color: var(--text); font-size: 13.5px; }
  .bubbles-head .spacer { margin-left: auto; }
  .bubbles-head .exportbtn { border: 1px solid var(--border); border-radius: 7px;
         background: var(--card); color: var(--text-dim); padding: 5px 10px;
         font-size: 12.5px; cursor: pointer; }
  .bubbles-body { display: grid; grid-template-columns: minmax(0, 1fr) 300px; }
  @media (max-width: 900px) { .bubbles-body { grid-template-columns: minmax(0, 1fr); } }
  .bubbles-chart { position: relative; padding: 14px; min-width: 0; }
  .bubbles-chart svg { display: block; width: 100%; height: auto; max-height: 68vh; }
  /* Animated geometry, so a metric change reads as the same circles resizing
     (dropped for reduced-motion users). */
  .bubbles-chart circle { transition: cx 320ms ease, cy 320ms ease, r 320ms ease; }
  .bubbles-chart text { transition: x 320ms ease, y 320ms ease; }
  @media (prefers-reduced-motion: reduce) {
    .bubbles-chart circle, .bubbles-chart text { transition: none; }
  }
  .bubbles-chart circle.bub { cursor: pointer; stroke: rgba(255,255,255,0.85); stroke-width: 1.5; }
  .bubbles-chart circle.bub:hover { stroke: var(--text); }
  .bubbles-chart g.sel circle.bub { stroke: var(--text); stroke-width: 3; }
  .bubbles-chart g.bubble:focus { outline: none; }
  .bubbles-chart g.bubble:focus-visible circle.bub { stroke: var(--link); stroke-width: 3.5; }
  .bubbles-chart text { pointer-events: none; text-anchor: middle; font-weight: 600;
         fill: #fff; font-family: inherit; }
  .bubbles-chart text.val { font-weight: 500; opacity: 0.92; }
  .bubbles-empty { padding: 30px 18px; font-size: 13px; color: var(--text-dim); line-height: 1.6; }
  .bubbles-side { border-left: 1px solid var(--border); background: var(--card-2);
         display: flex; flex-direction: column; min-width: 0; }
  @media (max-width: 900px) { .bubbles-side { border-left: 0; border-top: 1px solid var(--border); } }
  .bubbles-side .sidehead { padding: 11px 13px 8px 13px; }
  .bubbles-side input[type=text] { width: 100%; padding: 6px 9px; border-radius: 7px;
         border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 12.5px; }
  .bubbles-list { overflow: auto; max-height: 46vh; padding: 0 5px 8px 5px; }
  .bubbles-list button { display: block; width: 100%; text-align: left; border: 0;
         background: transparent; padding: 7px 8px; border-radius: 7px; cursor: pointer;
         font-size: 12.5px; color: var(--text); font-family: inherit; line-height: 1.45; }
  .bubbles-list button:hover { background: var(--card); }
  .bubbles-list button.on { background: #eef5ff; }
  .bubbles-list button .nm { font-weight: 600; }
  .bubbles-list button .sub { color: var(--text-dim); font-size: 11.5px; }
  .bubbles-list button .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         margin-right: 6px; vertical-align: middle; }
  .bubbles-list .nohits { padding: 10px 9px; font-size: 12px; color: var(--text-dim); }
  .bubbles-detail { border-top: 1px solid var(--border); padding: 11px 13px; font-size: 12.5px;
         line-height: 1.6; background: var(--card); }
  .bubbles-detail .dt-title { font-weight: 600; font-size: 13px; color: var(--text);
         word-break: break-word; }
  .bubbles-detail dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 10px; margin: 7px 0; }
  .bubbles-detail dt { color: var(--text-dim); }
  .bubbles-detail dd { margin: 0; font-variant-numeric: tabular-nums; }
  .bubbles-detail .hint { color: var(--text-dim); }
  .bubbles-notes { padding: 10px 15px; border-top: 1px solid var(--border);
         font-size: 11.5px; color: var(--text-dim); line-height: 1.6; background: var(--card-2); }
  .bubbles-notes li { margin: 2px 0; }
  .bubbles-notes ul { margin: 0; padding-left: 18px; }
  .bubbles-scale { display: flex; align-items: flex-end; gap: 9px; font-size: 11px;
         color: var(--text-dim); padding: 0 15px 11px 15px; }
  .bubbles-scale .sc { display: inline-block; border: 1px solid var(--text-dim); border-radius: 50%;
         opacity: 0.6; }
"""


# Above the toolbar and table, not behind a tab: the overview is read first.
BUBBLES_PANEL = """
  <div class="bubbles-wrap" id="bubblesWrap">
    <div class="bubbles-head">
      <strong class="bubbles-title">Overview</strong>
      <div class="metricswitch" id="bubbleViews"></div>
      <div class="metricswitch" id="bubbleMetrics"></div>
      <span class="spacer"></span>
      <button type="button" class="exportbtn" id="bubblesSvgBtn">Download SVG</button>
      <button type="button" class="exportbtn" id="bubblesPngBtn">Download PNG</button>
    </div>
    <div class="bubbles-body">
      <div class="bubbles-chart" id="bubblesChart"></div>
      <div class="bubbles-side">
        <div class="sidehead">
          <input type="text" id="bubblesSearch" placeholder="Search" aria-label="Search">
        </div>
        <div class="bubbles-list" id="bubblesList"></div>
        <div class="bubbles-detail" id="bubblesDetail"></div>
      </div>
    </div>
    <div class="bubbles-scale" id="bubblesScale"></div>
    <div class="bubbles-notes" id="bubblesNotes"></div>
  </div>
"""

