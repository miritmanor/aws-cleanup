"""Circle packing for the overview, in Python so both front ends draw the same served
layout. Unit space; area (not diameter) is proportional to the value."""

import math
from dataclasses import dataclass

from ...config import BUBBLE_METRIC_IDS

# The metrics as (id, label, meaning). The ids are in config.py for settings.py;
# METRIC_IDS is asserted equal to them.
METRICS = (
    ("resource_count", "Resources", "number of resources"),
    ("unused_pct", "% potentially unused", "share of the resources that looks unused"),
    ("unused_count", "Potentially unused", "resources that look unused"),
    ("cost", "Cost", "what AWS billed for the period"),
)

METRIC_IDS = tuple(m[0] for m in METRICS)

assert METRIC_IDS == BUBBLE_METRIC_IDS, (
    "present/bubbles.METRICS and config.BUBBLE_METRIC_IDS have diverged")

DEFAULT_METRIC = BUBBLE_METRIC_IDS[0]

# How much of the box the pack may fill. The remainder is for the stroke and
# for labels that sit just inside an edge circle.
_PADDING = 0.02


@dataclass(frozen=True)
class Bubble:
    """One circle, in unit space."""

    id: str
    x: float
    y: float
    r: float
    value: float

    def as_dict(self):
        # Rounded so the JSON is small and byte-stable for the golden tests.
        return {"id": self.id,
                "x": round(self.x, 6), "y": round(self.y, 6),
                "r": round(self.r, 6), "value": round(self.value, 6)}


def metric_value(summary, metric):
    """The number a circle's area represents, or None (never 0) when there is none."""
    if metric == "resource_count":
        return summary.resource_count or None
    if metric == "unused_pct":
        pct = summary.unused_pct
        return pct if pct else None
    if metric == "unused_count":
        return summary.unused or None
    if metric == "cost":
        cost = getattr(summary, "cost", None)
        if cost is None:
            return None
        amount = float(cost)
        # A negative total cannot be an area; credits are reported as text.
        return amount if amount > 0 else None
    raise ValueError(f"unknown metric: {metric}")


def _tangent_positions(a, b, r):
    """Centres where a circle of radius `r` touches both `a` and `b`; empty if none."""
    (ax, ay, ar), (bx, by, br) = a, b
    d1, d2 = ar + r, br + r
    dx, dy = bx - ax, by - ay
    d = math.hypot(dx, dy)
    if d == 0 or d > d1 + d2 or d < abs(d1 - d2):
        return ()
    # Standard two-circle intersection: project onto the line between centres,
    # then step off it perpendicularly.
    along = (d1 * d1 - d2 * d2 + d * d) / (2 * d)
    height_sq = d1 * d1 - along * along
    if height_sq < 0:
        return ()
    height = math.sqrt(height_sq)
    mx, my = ax + along * dx / d, ay + along * dy / d
    ox, oy = -dy / d * height, dx / d * height
    return ((mx + ox, my + oy), (mx - ox, my - oy))


def _fits(x, y, r, placed):
    """True when a circle here overlaps nothing placed; epsilon allows exact tangency."""
    for px, py, pr in placed:
        if math.hypot(px - x, py - y) < pr + r - 1e-9:
            return False
    return True


def _place(radii):
    """Greedy tangent packing of pre-sorted radii, closest to the origin first.
    Deterministic: candidates are ranked by (distance, x, y)."""
    placed = []
    for r in radii:
        if not placed:
            placed.append((0.0, 0.0, r))
            continue
        if len(placed) == 1:
            # Tangent to the first, along +x. Any direction packs identically;
            # a fixed one makes the result reproducible.
            px, py, pr = placed[0]
            placed.append((px + pr + r, py, r))
            continue
        best = None
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                for x, y in _tangent_positions(placed[i], placed[j], r):
                    if not _fits(x, y, r, placed):
                        continue
                    key = (round(math.hypot(x, y), 9), round(x, 9), round(y, 9))
                    if best is None or key < best[0]:
                        best = (key, x, y)
        if best is None:
            # No free tangent position: place beyond the current extent.
            edge = max(math.hypot(px, py) + pr for px, py, pr in placed)
            placed.append((edge + r, 0.0, r))
        else:
            placed.append((best[1], best[2], r))
    return placed


def pack(summaries, metric):
    """Circles for `summaries` under `metric`, in unit space, largest first. Items
    with no value get no circle."""
    if metric not in METRIC_IDS:
        raise ValueError(f"unknown metric: {metric}")

    sized = []
    for summary in summaries:
        value = metric_value(summary, metric)
        if value is not None and value > 0:
            sized.append((float(value), summary.bubble_id))
    if not sized:
        return ()

    # Largest first for a tight pack; ties broken on id for a stable layout.
    sized.sort(key=lambda item: (-item[0], item[1]))

    # sqrt for area proportionality, normalised against the largest so the
    # scale is set by the data rather than by an arbitrary constant.
    largest = sized[0][0]
    radii = [math.sqrt(value / largest) for value, _ in sized]

    placed = _place(radii)

    # Uniform scale, so circles stay circles.
    min_x = min(x - r for x, _, r in placed)
    max_x = max(x + r for x, _, r in placed)
    min_y = min(y - r for _, y, r in placed)
    max_y = max(y + r for _, y, r in placed)
    span = max(max_x - min_x, max_y - min_y)
    scale = (1.0 - 2 * _PADDING) / span if span else 1.0
    # Centre the pack's bounding box in the box, rather than anchoring it to a
    # corner: an off-centre chart reads as a bug.
    off_x = 0.5 - scale * (min_x + max_x) / 2
    off_y = 0.5 - scale * (min_y + max_y) / 2

    return tuple(
        Bubble(id=bubble_id,
               x=x * scale + off_x, y=y * scale + off_y, r=r * scale,
               value=value)
        for (value, bubble_id), (x, y, r) in zip(sized, placed))


def pack_all(summaries, metrics=METRIC_IDS):
    """Every metric's pack, keyed by metric id, computed together."""
    return {metric: [b.as_dict() for b in pack(summaries, metric)]
            for metric in metrics}
