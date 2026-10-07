"""HTML_TEMPLATE is rendered with str.format(), so every literal brace in it is DOUBLED;
the present/graph assets substituted in are NOT. rowKey() must mirror rows.member_key()."""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<link href="https://unpkg.com/tabulator-tables@5.6.1/dist/css/tabulator_simple.min.css" rel="stylesheet">
<script src="https://unpkg.com/tabulator-tables@5.6.1/dist/js/tabulator.min.js"></script>
{graph_head}{diagram_head}
<style>
  :root {{
    --bg: #f4f6f8; --card: #ffffff; --card-2: #f7f9fb; --border: #dfe4ea;
    --text: #212b36; --text-dim: #6b7684; --accent: #ff9900; --link: #1668c4;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         margin: 0; padding: 0 0 32px 0; background: var(--bg); color: var(--text); }}
  header.hero {{
    background: linear-gradient(135deg, #24303f 0%, #1b2430 60%, #161d27 100%);
    color: #f2f5f8; padding: 26px 30px 22px 30px; margin-bottom: 22px;
  }}
  h1 {{ font-size: 22px; margin: 0 0 5px 0; font-weight: 600; letter-spacing: 0.2px; }}
  h1 .accent {{ color: var(--accent); }}
  .subtitle {{ color: #a9b4c0; font-size: 13px; }}
  .content {{ padding: 0 30px; }}
  .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; }}
  .toolbar label {{ display: block; font-size: 11.5px; color: var(--text-dim); margin-bottom: 3px; }}
  .toolbar input, .toolbar select {{ padding: 8px 10px; border-radius: 8px; border: 1px solid var(--border);
         background: var(--card); color: var(--text); font-size: 13px; }}
  .toolbar input:focus, .toolbar select:focus {{ outline: 2px solid #cfe0f5; border-color: var(--link); }}
  .toolbar input[type=text] {{ width: 260px; }}
  .stat-pills {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
  .pill {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
         padding: 9px 15px; font-size: 12px; color: var(--text-dim);
         box-shadow: 0 1px 2px rgba(16,24,40,0.04); }}
  .pill b {{ color: var(--text); font-size: 15px; margin-right: 5px; }}
  .pill.risk-hi b {{ color: #c0392b; }}
  .pill.risk-lo b {{ color: #1e8449; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; font-size: 11.5px; color: var(--text-dim); }}
  .legend .swatch {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }}
  .graph-exports {{ margin-bottom: 16px; font-size: 11.5px; color: var(--text-dim); }}
  .graph-exports a {{ color: var(--link); text-decoration: none; }}
  .graph-exports a:hover {{ text-decoration: underline; }}
  .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 11px;
         overflow: hidden; box-shadow: 0 1px 3px rgba(16,24,40,0.06); }}
  #table {{ font-size: 12.5px; }}
  .tabulator {{ background: var(--card); border: none; }}
  .tabulator .tabulator-header {{ background: var(--card-2); border-bottom: 1px solid var(--border); color: var(--text); }}
  .tabulator .tabulator-header .tabulator-col {{ background: var(--card-2); border-right: 1px solid var(--border); }}
  .tabulator-row {{ background: var(--card) !important; border-bottom: 1px solid #eef1f4; }}
  .tabulator-row.tabulator-row-even {{ background: #fbfcfd !important; }}
  /* Rows are not clickable, so no pointer cursor (the theme sets one). */
  .tabulator-row:hover {{ background: #eff5fd !important; cursor: default !important; }}
  .tabulator-row.tabulator-selectable:hover {{ cursor: default !important; }}
  .tabulator-cell {{ cursor: text; }}
  .tabulator-cell a {{ cursor: pointer; }}
  /* Tabulator tooltips are its own popup element; this two-class selector outranks
     the theme's. */
  .tabulator-popup-container.tabulator-tooltip, .tabulator-tooltip {{
    max-width: 460px; white-space: pre-wrap; line-height: 1.5;
    background: #212b36; color: #f4f6f8; border: none; border-radius: 7px;
    padding: 8px 11px; font-size: 12px; box-shadow: 0 4px 14px rgba(16,24,40,0.22);
  }}
  .svc-badge {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }}
  .svc-badge .dot {{ width: 8px; height: 8px; border-radius: 50%; flex: none; }}
  a.console-link {{ color: var(--link); text-decoration: none; }}
  a.console-link:hover {{ text-decoration: underline; }}
  .risk-high {{ background: #fdecea !important; color: #a12b1e; }}
  .risk-medium {{ background: #fff5e0 !important; color: #8a6100; }}
  .risk-low {{ background: #eaf7ee !important; color: #1c6b39; }}
  .risk-unknown {{ background: #f2f4f6 !important; color: #5c6670; }}
  .tier-runtime {{ color: #1c6b39; font-weight: 600; }}
  .tier-deployment {{ color: #6b4fa8; font-weight: 600; }}
  .bill-cost {{ color: #a12b1e; font-weight: 600; }}
  .bill-indirect {{ color: #8a6100; font-weight: 600; }}
  .bill-usage {{ color: #1668c4; }}
  .bill-free {{ color: #8b949e; }}
  .del-btn {{ padding: 3px 9px; font-size: 11px; border-radius: 5px; border: 1px solid var(--border);
         background: #f7f9fb; color: #9aa3ad; cursor: not-allowed; }}
  .tabulator-group {{ background: #eef2f7 !important; color: var(--text) !important;
         border-top: 1px solid var(--border) !important; border-bottom: 1px solid var(--border) !important;
         font-weight: 600; }}
  .tabulator-group:hover {{ background: #e4ebf3 !important; }}
  .tabulator-group .grp-count {{ color: var(--text-dim); font-weight: 400; margin-left: 7px; }}
  .tabulator-group .grp-cost {{ color: #9a6a00; font-weight: 500; margin-left: 10px; }}
  footer {{ margin-top: 18px; font-size: 11.5px; color: var(--text-dim); line-height: 1.65; }}
{graph_css}{diagram_css}{bubbles_css}
</style>
</head>
<body>
<header class="hero">
  <h1>&#9729; <span class="accent">AWS</span> Resource Inventory</h1>
  <div class="subtitle">Generated {generated_at} &middot; {row_count} resources across {region_count} region(s)
    &middot; data is a point-in-time snapshot, re-run the scan script to refresh</div>
</header>
<div class="content">

  <div class="stat-pills">
    <span class="pill">Total resources: <b>{row_count}</b></span>
    <span class="pill">STALE: <b>{stale_count}</b></span>
    <span class="pill risk-hi">HIGH risk: <b>{high_risk}</b></span>
    <span class="pill risk-lo">LOW risk (cleanup candidates): <b>{low_risk}</b></span>
  </div>
{coverage_banner}
{billing_section}
{bubbles_panel}
  <div class="toolbar">
{graph_switch}
    <div>
      <label for="search">Search (any column)</label>
      <input type="text" id="search" placeholder="e.g. i-0abc, myapp, us-west-2...">
    </div>
    <div>
      <label for="groupSelect">Group by</label>
      <select id="groupSelect">
        <option value="">(none)</option>
        <option value="project_id" selected>Project</option>
        <option value="service">Service</option>
        <option value="region">Region</option>
        <option value="flag">Flag</option>
        <option value="risk_if_removed">Risk if removed</option>
        <option value="tier">Runtime / deployment</option>
      </select>
    </div>
    <div>
      <label for="tierFilter">Runtime / deployment</label>
      <select id="tierFilter">
        <option value="">(all)</option>
        <option value="runtime">runtime - part of the running application</option>
        <option value="deployment">deployment - machinery that put it there</option>
        <option value="__none__">unclassified - no evidence either way</option>
      </select>
    </div>
    <div>
      <label for="billingFilter">Billing</label>
      <select id="billingFilter">
        <option value="">(all)</option>
        <option value="cost">cost - charged just to exist</option>
        <option value="indirect">indirect - holds something billable</option>
        <option value="usage">usage - only when used</option>
        <option value="free">free - no charge</option>
      </select>
    </div>
  </div>

  <div class="legend" id="legend"></div>

{graph_panel}{diagram_panel}
  <div class="graph-exports">
    Same architecture diagram, written beside this file as plain text:
    <a href="{mermaid_file}">{mermaid_file}</a> (Mermaid, the source behind the Diagram view) -
    written even when the panels above are off.
  </div>

  <div class="table-wrap"><div id="table"></div></div>

  <footer>
    <b>Cost to keep</b> answers "does this cost me anything just by existing?", which is a different
    question from whether it's stale - a stale free resource is clutter, a stale billable one is money:
    <span class="bill-cost">yes - always</span> charged for existing, so deleting saves real money;
    <span class="bill-indirect">yes - indirect</span> free itself but keeps something billable alive;
    <span class="bill-usage">only when used</span> charged per request/hour of running, so idle costs
    little; <span class="bill-free">no charge</span> free, so removing it is housekeeping only.
    Hover any cell in that column for the reason specific to that resource type. These are the standard
    charging models, not your actual bill - the cost columns carry figures from your own Cost
    Explorer data, over a {cost_lookback_days}-day window of charges ALREADY INCURRED. That is not a
    monthly forecast and not a saving: the "How it was arrived at" column says what each figure is,
    and a blank there means this scan will not put a number on that resource rather than that it is
    free.<br>
    <b>Runtime / deployment</b> splits a project into the application and the machinery that
    deployed it - the bucket holding zipped Lambda code, the functions that run once during a
    push. It is a separate axis from the project group, not a finer one, so a project stays one
    project. Every verdict comes from something AWS states (Amplify naming its own artifact
    bucket, the CloudFormation logical id, which category stack provisioned a resource, or a
    link from something serving traffic); hover the cell for that reason. A resource with no
    such evidence is assumed to be runtime and has no reason to show.<br>
    Hover any truncated cell for its full text; click a Resource ID to open it in the AWS console.
    "Risk if removed", "Connections" and "Why grouped" are built only from what the scan reads -
    verify actual dependents before deleting anything. The Remove button is inactive: this page is a
    static file with no server behind it.<br>
    The Graph view draws the same links this table reports: a solid line grouped its two endpoints
    into one project, a dashed one was detected but deliberately not trusted to group, a faint dotted
    one means the two share an attribute (a tag, VPC, IAM role or name word) with no actual link
    between them, and a ghost node is something referenced here that this scan did not find. Clicking
    a node narrows the canvas and the table to it and its neighbours - that focus stays on until
    you clear it.
    Use the Layout control there to change how the nodes are placed: Clusters shows the shape of
    the account, Layered puts each project in its own box and orders it by dependency direction.<br>
    The Diagram view draws the same links again as a static hierarchical picture, which is the
    thing a force layout cannot do - but it is a picture: no hovering, no inspector, no filtering.
    Bold nodes are compute, database or storage - the application's main shape; muted nodes
    (load balancer, API Gateway, SQS/SNS, Amplify) are supporting context, not excluded, just
    drawn quieter.
    All of the report's data is embedded in this file, but the table, graph and diagram libraries
    load from unpkg.com; without network access each shows a message instead.
  </footer>
</div>

<script>
const ROWS = {rows_json};
const SERVICE_META = {service_meta_json};
const GRAPH = {graph_json};
const BUBBLES = {bubbles_json};
const DIAGRAM_SOURCES = {diagram_sources_json};
const DIAGRAM_PROJECT_NAMES = {diagram_names_json};
const DIAGRAM_FILES = {diagram_files_json};
const DIAGRAM_DRAWIO_FILES = {diagram_drawio_files_json};
const DEFAULT_META = {{color: "#888888"}};

function metaFor(service) {{ return SERVICE_META[service] || DEFAULT_META; }}
// The AWS service (EC2, S3...) a resource type is filed under, for the Service filter.
function awsServiceOf(type) {{ return metaFor(type).service || type; }}
const AWS_SERVICES = [...new Set(ROWS.map(r => awsServiceOf(r.service)))].sort();
// The Flag filter matches usage_state, the flag's verdict word as a value.
const USAGE_OPTIONS = {{unused: "STALE", active: "ACTIVE", unknown: "UNKNOWN"}};

function riskClass(v) {{
  if (!v) return "";
  if (v.startsWith("HIGH")) return "risk-high";
  if (v.startsWith("MEDIUM")) return "risk-medium";
  if (v.startsWith("LOW")) return "risk-low";
  return "risk-unknown";
}}

// Build the category-color legend once, from whatever services actually
// appear in this scan (not the full fixed list), so it stays relevant.
(function buildLegend() {{
  const seen = new Map();
  ROWS.forEach(r => {{
    const m = metaFor(r.service);
    if (!seen.has(m.category)) seen.set(m.category, m.color);
  }});
  const html = Array.from(seen.entries()).map(([cat, color]) =>
    `<span><span class="swatch" style="background:${{color}}"></span>${{cat}}</span>`
  ).join("");
  document.getElementById("legend").innerHTML = html;
  // The graph panel's own copy, shown only in fullscreen - see graph-color-legend
  // in panel.py. Absent entirely when "graph_in_html" is off.
  const graphLegend = document.getElementById("graphColorLegend");
  if (graphLegend) graphLegend.innerHTML = html;
}})();

// Projects are grouped by project_id and titled with the name.
let groupField = "project_id";
function groupHeaderWithCost(value, count, data) {{
  const totalCost = data.reduce((sum, r) => sum + (parseFloat(r.est_monthly_cost_usd) || 0), 0);
  const costPart = totalCost > 0 ? `<span class="grp-cost">~$${{totalCost.toFixed(2)}}/mo</span>` : "";
  const title = groupField === "project_id"
    ? ((value && data[0] && data[0].project_group) || value || "(no project)")
    : (value || "(none)");
  return `${{title}}<span class="grp-count">${{count}} resource${{count === 1 ? "" : "s"}}</span>${{costPart}}`;
}}

const table = new Tabulator("#table", {{
  data: ROWS,
  layout: "fitDataStretch",
  height: "72vh",
  pagination: false,
  movableColumns: true,
  groupBy: "project_id",
  groupHeader: groupHeaderWithCost,
  // Display-only rows, so no clickable-looking cursor.
  selectableRows: false,
  // Truncated cells show their full value on hover. Set once here rather than
  // per column so every column (including ones added later) gets it.
  columnDefaults: {{tooltip: true}},
  columns: [
    {{title: "Service", field: "service", width: 170,
      headerFilter: "list", headerFilterParams: {{values: AWS_SERVICES, clearable: true}},
      headerFilterFunc: (want, type) => awsServiceOf(type) === want,
      formatter: function(cell) {{
        const v = cell.getValue() || "";
        const m = metaFor(v);
        return `<span class="svc-badge"><span class="dot" style="background:${{m.color}}"></span>${{v}}</span>`;
      }}}},
    {{title: "Region", field: "region", headerFilter: "input", width: 100}},
    {{title: "Resource ID", field: "resource_id", headerFilter: "input", width: 200,
      formatter: function(cell) {{
        const d = cell.getRow().getData();
        const label = cell.getValue();
        if (d.console_url) {{
          return `<a class="console-link" href="${{d.console_url}}" target="_blank" rel="noopener">${{label}}</a>`;
        }}
        return label;
      }}}},
    {{title: "Name", field: "name", headerFilter: "input", width: 140}},
    {{title: "Project", field: "project_group", headerFilter: "input", width: 130}},
    {{title: "Runtime / deployment", field: "tier", headerFilter: "input", width: 130,
      headerTooltip: "Which half of its project this is: the running application, or the " +
                     "machinery that deployed it. Runtime is also the default when nothing " +
                     "says otherwise. Hover a cell for the reason, where a rule decided it.",
      formatter: function(cell) {{
        const v = cell.getValue() || "";
        if (v) cell.getElement().classList.add("tier-" + v);
        return v || "&mdash;";
      }},
      tooltip: function(e, cell) {{
        const d = cell.getRow().getData();
        return d.why_tier || false;
      }}}},
    {{title: "Flag", field: "flag", width: 150,
      headerFilter: "list", headerFilterParams: {{values: USAGE_OPTIONS, clearable: true}},
      headerFilterFunc: (want, flag, row) => row.usage_state === want}},
    {{title: "Cost to keep", field: "billing", headerFilter: "input", width: 125,
      headerTooltip: "What it costs to KEEP this resource, separate from whether it is stale. " +
                     "Hover a cell for the specific reason.",
      formatter: function(cell) {{
        const v = cell.getValue() || "";
        const label = {{
          cost:     "yes - always",
          indirect: "yes - indirect",
          usage:    "only when used",
          free:     "no charge",
        }}[v] || v;
        cell.getElement().classList.add("bill-" + v);
        return label;
      }},
      tooltip: function(e, cell) {{
        const d = cell.getRow().getData();
        const why = {{
          cost:     "Billed just for existing - deleting this saves real money.",
          indirect: "Free itself, but it keeps something billable alive.",
          usage:    "Billed only when it runs or serves traffic - idle costs little or nothing.",
          free:     "No charge at all - removing it is housekeeping, not a saving.",
        }}[d.billing] || "";
        return why + (d.billing_note ? "\\n\\n" + d.service + ": " + d.billing_note : "");
      }}}},
    {{title: "{cost_column_label}", field: "est_monthly_cost_usd", width: 150, hozAlign: "right"}},
    {{title: "How it was arrived at", field: "cost", width: 170}},
    {{title: "Cost notes", field: "cost_notes", width: 220}},
    {{title: "Risk if removed", field: "risk_if_removed", headerFilter: "input", width: 170,
      formatter: function(cell) {{
        cell.getElement().classList.add(riskClass(cell.getValue()));
        return cell.getValue();
      }}}},
    {{title: "Connections", field: "connections", headerFilter: "input", width: 260}},
    {{title: "Why grouped", field: "why_grouped", headerFilter: "input", width: 240}},
    {{title: "Description", field: "description", headerFilter: "input", width: 200}},
    {{title: "Tags", field: "tags", headerFilter: "input", width: 180}},
    {{title: "Created", field: "created", width: 170}},
    {{title: "Last used", field: "last_used", width: 170}},
    {{title: "Last used (days)", field: "last_used_days", width: 100, hozAlign: "right"}},
    {{title: "Notes", field: "notes", width: 260}},
    {{title: "Remove", field: "resource_id", width: 90, headerSort: false,
      formatter: function() {{
        return `<button class="del-btn" disabled title="Not implemented yet - phase 2 requires a live server, this report is static">Remove</button>`;
      }}}},
  ],
}});

// Must mirror member_key() in the Python exactly - it is the join between a
// graph node and its table row, and a mismatch filters the table to nothing.
function rowKey(d) {{ return d.service + ":" + d.region + ":" + d.resource_id; }}

// Row keys when a graph node is focused, else null. Declared here because applyFilters
// reads it and GRAPH_JS is appended later.
let graphFocus = null;

// One combined predicate: each Tabulator setFilter REPLACES the active filter.
function applyFilters() {{
  const q = document.getElementById("search").value.toLowerCase();
  const bill = document.getElementById("billingFilter").value;
  const tier = document.getElementById("tierFilter").value;
  if (!q && !bill && !tier && !graphFocus) {{ table.clearFilter(); return; }}
  table.setFilter(function(data) {{
    if (graphFocus && !graphFocus.has(rowKey(data))) return false;
    if (bill && data.billing !== bill) return false;
    // "__none__" asks for unclassified rows ("" already means "(all)").
    if (tier === "__none__" ? !!data.tier : (tier && data.tier !== tier)) return false;
    if (q && !Object.values(data).some(v => String(v ?? "").toLowerCase().includes(q))) return false;
    return true;
  }});
}}

document.getElementById("search").addEventListener("input", applyFilters);
document.getElementById("billingFilter").addEventListener("change", applyFilters);
document.getElementById("tierFilter").addEventListener("change", applyFilters);

document.getElementById("groupSelect").addEventListener("change", function(e) {{
  groupField = e.target.value;
  table.setGroupBy(e.target.value || false);
}});
{graph_js}{bubbles_js}
</script>
</body>
</html>
"""


# Re-exported so `from .template import SERVICE_META` keeps working.
from .service_meta import SERVICE_META  # noqa: E402,F401
