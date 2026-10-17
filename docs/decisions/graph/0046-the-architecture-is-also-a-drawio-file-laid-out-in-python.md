# 0046 - The architecture diagram is also a native draw.io file, laid out in Python

Status: accepted

## Context

The `.mmd` download names its icons (`icon: "aws:lambda"`) from a pack this
tool registers at render time (`present/graph/aws_icons_data.py`).
draw.io's Mermaid import has never registered that pack, so the diagram arrives
without icons. The file can't carry the pack, so this can't be fixed inside
the `.mmd`.

Two ways to get the diagram into draw.io were considered. An SVG export keeps
the icons, but draw.io imports it as one picture you cannot edit. A native
`.drawio` file keeps everything editable, but it has to say where every shape
goes, because draw.io does not lay a file out when it opens it.

## Decision

**Write a native `.drawio` (mxfile) file from the same architecture graph**, in
`present/graph/drawio.py`:

- Nodes use **draw.io's own AWS4 shapes**
  (`shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda`) in the fill
  colour draw.io's "AWS" sidebar uses for that category, so they look and
  behave exactly like shapes dragged from that sidebar. The names and colours
  were checked against draw.io's `Sidebar-AWS4.js`. `DRAWIO_SHAPES` maps each
  drawn type; `test_every_drawn_type_has_a_draw_io_shape` fails if one is
  missing.
- One box per project inside an **"AWS Cloud" group** (draw.io's
  `group_aws_cloud_alt`). Unowned resources, and other projects' resources in a
  scoped view, go in a dashed box. `present/graph/bands.py` decides the boxes
  for both the `.mmd` and the `.drawio`, so the two cannot disagree.
- Lines carry the verb and are dashed where the Mermaid line is dotted.

**The layout is Python arithmetic** (`drawio_layout.py`), for the same reason
as the bubble pack (0041):
- Columns follow an AWS reference diagram's reading order: edge → entry →
  compute → messaging → data.
- Rows in each column are ordered by the mean row of their neighbours in the
  columns already placed (one barycentre sweep), to cut crossings.
- A column longer than `MAX_ROWS` wraps into the next, so a band grows wide
  rather than tall.
- The layout is deterministic: the same graph gives the same file.

It is the **seventh output**, `aws_architecture.drawio`, plus one per project
beside the per-project `.mmd` files. It is served as
`GET /api/export/architecture.drawio?project_id=` and linked from both front
ends.

## Consequences

- The layout is simple, not optimal: one sweep, no edge-routing hints.
  draw.io's orthogonal connectors route the lines, and everything can be
  rearranged by hand. That is the point of an editable file.
- A new drawn type needs a `DRAWIO_SHAPES` entry and a column in `COLUMN_OF`
  (unknown types sit with compute). The roadmap's checklist step 6 covers it.
- The VPC and subnet boxes planned for project-scoped views (roadmap step 2)
  belong here as draw.io's `group_vpc2` and subnet groups, once VPCs are
  scanned.
