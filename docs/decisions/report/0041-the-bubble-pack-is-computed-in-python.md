# 0041 - The project overview's circles are packed in Python

Status: accepted

## Context

Phase 9 adds a project overview: one circle per project, area proportional to a
metric the reader chooses. The obvious way to build it is
[`d3-hierarchy`'s `pack()`](https://d3js.org/d3-hierarchy/pack) in the browser,
and that is what the original plan specified — a small framework-independent
TypeScript chart module, wrapped by React in the web app and shipped as a
prebuilt bundle inside the Python package for the generated HTML report.

That plan runs into three things at once.

**The standing rule** (AGENTS.md): where Python already decides something, the
web app is served that decision through a thin endpoint rather than re-deriving
it in TypeScript. `GET /api/service-meta` and `GET /api/mermaid` are the
pattern. A circle's position and radius are a decision.

**The precedent.** A JavaScript layout engine *was* tried for the old Graph
tab (removed since). Cytoscape's `cose` overlapped its compound
boxes, `fcose` threw from inside its own minified dependency on this project's
golden payload, and tuning its options fixed one graph and broke others. The fix
was to place extents with arithmetic instead of negotiating them with forces.
Non-overlap became a property of the arithmetic. Two overlapping circles would imply shared
membership that the grouping pass explicitly refused to claim, which is the same
class of false statement.

**Packaging.** `aws_resource_audit/` depends on boto3 and nothing else, and the
generated report has to work on a machine that has never had Node installed.
Shipping a bundle means a build step, a committed artifact or both, plus a rule
about which is authoritative.

## Decision

**`present/bubbles/pack.py` computes the circles. Both front ends draw what it
returns.**

- Radius is `sqrt(value)`, normalised against the largest circle, so **area** is
  proportional to the value.
- Placement is greedy tangent packing: sort by radius descending, place the
  largest at the origin, then put each next circle where it touches two
  already-placed circles without overlapping any, as close to the origin as such
  a position gets.
- Circles come out in **unit space** — a 1×1 box, drawn with
  `viewBox="0 0 1000 1000"`. One computation serves every viewport and both
  front ends; being responsive is the browser's job rather than a measurement
  the packer has to be told.
- `pack_all()` returns all four metrics at once. Switching metric is a
  re-render from data already in the page, and the CLI report — which has no
  server to ask — gets the same four for free.

`ProjectBubblesPanel.tsx` and `present/bubbles/panel.py` both put the served
numbers straight into `<circle>` attributes. Neither contains a layout.

## Consequences

**"The API and the CLI agree" is true by construction** rather than by a test
asserting that two renderers agree. There is one packer.

**No JavaScript chart library anywhere**, and nothing to ship. The overview is
the one panel in the generated report that needs no CDN at all: inline SVG from
embedded JSON, correct with the network off. The report's existing Tabulator,
Cytoscape and Mermaid dependencies are untouched — bundling those is separate
work and this decision does not start it.

**The pack is deterministic**, and tested to be. Nothing in it is random, and
candidate positions are ranked by a total order (distance, then x, then y), so
an exact tie cannot break differently on a different run. Input order does not
affect the result either — ties among equal values break on `project_id` —
because nothing downstream preserves an order the packer could depend on.

**The packing is not as tight as D3's.** The front-chain algorithm D3 uses
maintains a hull and is measurably denser on large inputs. On a few dozen
projects the difference is cosmetic, and the greedy version is about sixty lines
against several hundred. If density ever matters more than that, the front-chain
algorithm is a drop-in behind the same `pack(summaries, metric)` signature.

**Placement is O(n²) candidate pairs per circle.** Fine for the tens of projects
an account has; it would not be fine for thousands, and if that ever happens the
answer is the same upgrade.

**A zero, negative or unavailable value gets no circle at all** — not a
minimum-size one. A minimum-size bubble is a quantitative claim about a quantity
there isn't one of. Those projects stay visible in the companion list beside the
chart, which is also how a circle too small to click is selected.
