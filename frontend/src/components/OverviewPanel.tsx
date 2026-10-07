// The overview: one circle per AWS service or project. Everything is computed in
// Python (GET /api/overview); selecting a circle filters no other tab.

import { Fragment, useMemo, useState } from "react";
import type { Overview, OverviewItem, OverviewView, ServiceMeta } from "../api/types";

interface Props {
  overview: Overview | null;
  serviceMeta: ServiceMeta;
}

const S = 1000; // unit space -> viewBox units

// Projects keep a colour hashed from the id, so the eye can follow one circle
// between metrics. Services and bill lines use their AWS category's colour.
const PALETTE = [
  "#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#76b7b2",
  "#e15759", "#9c755f", "#edc948", "#8cd17d", "#d37295",
];

function colourOf(item: OverviewItem, serviceMeta: ServiceMeta): string {
  if (item.resource_types) {
    return serviceMeta[item.resource_types[0]]?.color ?? "#8c96a3";
  }
  let h = 0;
  for (let i = 0; i < item.id.length; i += 1) h = (h * 31 + item.id.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function fmtMoney(amount: string | null | undefined, currency: string): string {
  if (amount === null || amount === undefined) return "—";
  const n = Number(amount);
  if (Number.isNaN(n)) return amount;
  return `${Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2)} ${currency}`.trim();
}

function fmtPct(p: number | null): string {
  return p === null ? "—" : `${p.toFixed(0)}%`;
}

/** The short string that goes INSIDE a circle. */
function shortValue(p: OverviewItem, metric: string): string {
  if (metric === "resource_count") return String(p.resource_count);
  if (metric === "unused_pct") return fmtPct(p.unused_pct);
  if (metric === "unused_count") return String(p.unused);
  return fmtMoney(p.cost, "");
}

function longValue(p: OverviewItem, metric: string, currency: string): string {
  if (metric === "resource_count") return `${p.resource_count} resources`;
  if (metric === "unused_pct") return `${fmtPct(p.unused_pct)} potentially unused`;
  if (metric === "unused_count") return `${p.unused} potentially unused`;
  return fmtMoney(p.cost, currency);
}

const BUCKET_KINDS = new Set(["shared", "ambiguous", "unassigned"]);

export function OverviewPanel({ overview, serviceMeta }: Props) {
  // null means "whatever the payload says", resolved once it has arrived;
  // seeding from the payload here would freeze the value before the fetch.
  const [pickedView, setPickedView] = useState<string | null>(null);
  const [pickedMetric, setPickedMetric] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  // What the detail box shows while pointing, as distinct from the selection.
  const [hovered, setHovered] = useState<string | null>(null);

  const view: OverviewView | null = useMemo(() => {
    const views = overview?.views ?? [];
    const wanted = pickedView ?? overview?.default_view;
    return views.find((v) => v.id === wanted) ?? views[0] ?? null;
  }, [overview, pickedView]);

  const byId = useMemo(
    () => new Map((view?.items ?? []).map((p) => [p.id, p])),
    [view],
  );

  if (!overview || !view) {
    return (
      <div className="bubbles-panel">
        <p className="empty">Nothing to show yet. The overview appears after a scan.</p>
      </div>
    );
  }

  const available = (m: string) => (view.packs[m] ?? []).length > 0;
  const offered = view.metrics.map((m) => m[0]);
  // What the user picked, else the configured default, else the first metric
  // this view has anything to draw for.
  const wanted = pickedMetric ?? overview.default_metric;
  const metric = offered.includes(wanted) && available(wanted)
    ? wanted
    : offered.find(available) ?? offered[0];

  const circles = view.packs[metric] ?? [];
  const detailId = hovered ?? selected;
  const detail = detailId ? byId.get(detailId) ?? null : null;
  const listed = (view.lists[metric] ?? [])
    .map((id) => byId.get(id))
    .filter((p): p is OverviewItem =>
      !!p && (!query || p.display_name.toLowerCase().includes(query.toLowerCase())));
  const labelOf = (m: string) => view.metrics.find((x) => x[0] === m)?.[1] ?? m;
  const toggle = (id: string) => setSelected(selected === id ? null : id);

  return (
    <div className="bubbles-panel">
      <div className="bubbles-head">
        <div className="bubbles-metrics">
          {overview.views.map((v) => (
            <button
              key={v.id}
              type="button"
              className={v.id === view.id ? "on" : ""}
              onClick={() => { setPickedView(v.id); setSelected(null); }}
            >
              {v.label}
            </button>
          ))}
        </div>
        <div className="bubbles-metrics">
          {view.metrics.map(([id, label, tip]) => (
            <button
              key={id}
              type="button"
              className={id === metric ? "on" : ""}
              disabled={!available(id)}
              title={available(id) ? tip : `${tip} — nothing to draw in this scan`}
              onClick={() => { setPickedMetric(id); setSelected(null); }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="bubbles-body">
        <div className="bubbles-chart">
          {circles.length === 0 ? (
            <p className="empty">{unavailableReason(metric, overview)}</p>
          ) : (
            <svg viewBox={`0 0 ${S} ${S}`} role="img"
                 aria-label={`Overview ${view.label.toLowerCase()}, ${labelOf(metric)}`}>
              {circles.map((b) => {
                const p = byId.get(b.id);
                if (!p) return null;
                const cx = b.x * S;
                const cy = b.y * S;
                const r = b.r * S;
                const name = p.display_name;
                // Labels only where they fit; the side list names everything.
                const size = Math.min(r * 0.42, (r * 1.7) / Math.max(name.length, 1));
                const fits = size >= 9;
                const room = r > 46;
                return (
                  <g
                    key={b.id}
                    className={`bubble${selected === b.id ? " sel" : ""}`}
                    tabIndex={0}
                    role="button"
                    aria-label={`${name}, ${longValue(p, metric, overview.currency)}`}
                    onClick={() => toggle(b.id)}
                    onMouseEnter={() => setHovered(b.id)}
                    onMouseLeave={() => setHovered(null)}
                    onFocus={() => setHovered(b.id)}
                    onBlur={() => setHovered(null)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(b.id); }
                    }}
                  >
                    <circle cx={cx} cy={cy} r={r} fill={colourOf(p, serviceMeta)} />
                    {fits && (
                      <text x={cx} y={room ? cy - 3 : cy + size * 0.35} fontSize={size}>
                        {name}
                      </text>
                    )}
                    {fits && room && (
                      <text className="val" x={cx} y={cy + size * 1.15} fontSize={size * 0.85}>
                        {shortValue(p, metric)}
                      </text>
                    )}
                  </g>
                );
              })}
            </svg>
          )}
          <p className="bubbles-scale">
            Area is proportional to {labelOf(metric).toLowerCase()}. Sizes are
            comparable within this view only.
            {metric === "cost" && overview.cost_queried &&
              ` Billing period ${overview.period_start} to ${overview.period_end}.`}
          </p>
        </div>

        <div className="bubbles-side">
          <input
            type="text"
            value={query}
            placeholder={`Search ${view.noun}s`}
            aria-label={`Search ${view.noun}s`}
            onChange={(e) => setQuery(e.target.value)}
          />
          {/* Everything this metric is about, circle or not: a zero or unknown
              value gets no bubble, and this list is where it stays visible. */}
          <div className="bubbles-list">
            {listed.length === 0 ? (
              <p className="empty">Nothing matches that.</p>
            ) : (
              listed.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={selected === p.id ? "on" : ""}
                  onClick={() => toggle(p.id)}
                >
                  <span className="dot" style={{ background: colourOf(p, serviceMeta) }} />
                  <span className="nm">{p.display_name}</span>
                  {BUCKET_KINDS.has(p.kind) && <span className="sub"> ({p.kind})</span>}
                  <br />
                  <span className="sub">
                    {p.kind === "bill"
                      ? fmtMoney(p.cost, overview.currency)
                      : `${p.resource_count} resources` + (p.resource_count > 0
                        ? (p.all_unknown ? " · usage unknown" : ` · ${p.unused} unused / ${p.unknown} unknown`)
                        : "")}
                  </span>
                </button>
              ))
            )}
          </div>

          <div className="bubbles-detail">
            {!detail ? (
              <p className="sub">Point at a {view.noun} to see its numbers.</p>
            ) : (
              <Detail item={detail} currency={overview.currency} />
            )}
          </div>
        </div>
      </div>

      {view.notes.length > 0 && (
        <ul className="bubbles-notes">
          {view.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

function Detail({ item, currency }: { item: OverviewItem; currency: string }) {
  return (
    <>
      <div className="dt-title">{item.display_name}</div>
      {item.all_unknown && (
        <p className="sub">
          No readable usage telemetry for anything here — 0 potentially unused
          means nothing could be measured, not that nothing is idle.
        </p>
      )}
      {item.why && <p className="sub">{item.why}</p>}
      <dl>
        {item.kind === "bill" ? (
          <>
            <dt>Billed</dt>
            <dd>{fmtMoney(item.cost, currency)}</dd>
          </>
        ) : (
          <>
            <dt>Resources</dt>
            <dd>{item.resource_count}</dd>
            <dt>Potentially unused</dt>
            <dd>{item.resource_count ? `${item.unused} (${fmtPct(item.unused_pct)})` : "—"}</dd>
            <dt>Active</dt>
            <dd>{item.active}</dd>
            <dt>Usage unknown</dt>
            <dd>{item.unknown}</dd>
          </>
        )}
        {(item.type_counts ?? []).map(([type, count]) => (
          <Fragment key={type}>
            <dt>{type}</dt>
            <dd>{count}</dd>
          </Fragment>
        ))}
        {item.grouping_method && (
          <>
            <dt>Grouped by</dt>
            <dd>{item.grouping_method}</dd>
          </>
        )}
      </dl>
    </>
  );
}

/** Why a metric has nothing to draw. A scan that never asked about cost and an
 *  account with nothing idle are opposite answers and must not read the same. */
function unavailableReason(metric: string, overview: Overview): string {
  if (metric === "cost" && !overview.cost_queried) {
    return "Cost was not queried for this scan, so there is nothing to size " +
      "circles by. Scan again without “Skip cost lookup”.";
  }
  if (metric === "unused_pct" || metric === "unused_count") {
    return "Nothing in this account is flagged as potentially unused, so there " +
      "is nothing to draw. That is not the same as everything being in use — " +
      "check the list for usage that is unknown.";
  }
  return "Nothing has a value for this view.";
}
