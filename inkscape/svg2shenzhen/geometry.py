# Copyright (C) 2026 David
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
"""
Vector geometry of a layer in KiCad millimetres.

KiCad coordinates here have their origin at the centre of the page,
x to the right and y down (as in SVG), which is where the traced
artwork layers are centred too. Every shape is read with all its
transforms (its own, its groups', its layer's), in real millimetres
whatever the document's units and viewBox.
"""

import itertools
import math

import inkex

MM_PER_PX = 25.4 / 96.0


class Mapping:
    """User units of the document to KiCad millimetres."""

    def __init__(self, svg):
        x, y, width, height = svg.get_viewbox()
        if not width or not height:
            width, height = svg.viewport_width, svg.viewport_height
        self.mm_per_uu = svg.scale * MM_PER_PX  # svg.scale: px per user unit
        self.cx = x + width / 2.0
        self.cy = y + height / 2.0

    def point(self, x, y):
        return ((x - self.cx) * self.mm_per_uu, (y - self.cy) * self.mm_per_uu)

    def length(self, value):
        return value * self.mm_per_uu


def shapes(layer):
    """Every drawn shape in a layer (not groups, not hidden ones)."""
    for node in layer.descendants():
        if node is layer or not isinstance(node, inkex.ShapeElement):
            continue
        if isinstance(node, (inkex.Group, inkex.TextElement, inkex.Tspan)):
            continue
        if any(
            a.style.get("display") == "none"
            for a in [node, *node.ancestors()]
            if isinstance(a, inkex.ShapeElement)
        ):
            continue
        yield node


def circle_of(node):
    """(cx, cy, r) in user units after all transforms, if `node` is a
    circle (or a round ellipse) whose transforms keep it round."""
    if isinstance(node, inkex.Circle):
        cx, cy, rx, ry = node.center.x, node.center.y, node.radius, node.radius
    elif isinstance(node, inkex.Ellipse):
        cx, cy = node.center.x, node.center.y
        rx, ry = node.radius.x, node.radius.y
        if not math.isclose(rx, ry, rel_tol=1e-6):
            return None
    else:
        return None
    m = node.composed_transform()
    sx = math.hypot(m.a, m.b)
    sy = math.hypot(m.c, m.d)
    shear = m.a * m.c + m.b * m.d
    if not math.isclose(sx, sy, rel_tol=1e-6) or abs(shear) > 1e-9 * sx * sy:
        return None
    centre = m.apply_to_point((cx, cy))
    return centre.x, centre.y, rx * sx


def subpaths(node):
    """The outline of a shape after all transforms, as a list of
    subpaths; each is a list of segments ('line', p0, p1) or
    ('curve', p0, c1, c2, p1) in user units."""
    path = node.path.transform(node.composed_transform()).to_absolute()
    out = []
    for sub in path.to_superpath():
        segments = []
        for a, b in itertools.pairwise(sub):
            p0, c1, c2, p1 = a[1], a[2], b[0], b[1]
            straight = _same(p0, c1) and _same(c2, p1)
            if straight:
                if not _same(p0, p1):
                    segments.append(("line", tuple(p0), tuple(p1)))
            else:
                segments.append(("curve", tuple(p0), tuple(c1), tuple(c2), tuple(p1)))
        if segments:
            out.append(segments)
    return out


def _same(a, b, eps=1e-9):
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def flatten(segment, tolerance):
    """Points along a segment, the curve split until each piece is
    within `tolerance` (user units) of a straight line."""
    if segment[0] == "line":
        return [segment[1], segment[2]]
    p0, c1, c2, p1 = segment[1:]
    chord = math.dist(p0, p1)
    hull = math.dist(p0, c1) + math.dist(c1, c2) + math.dist(c2, p1)
    steps = max(
        1, math.ceil(math.sqrt(max(hull - chord, 0) / max(tolerance, 1e-12)) * 2)
    )
    steps = min(steps, 512)
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        points.append(
            (
                u**3 * p0[0]
                + 3 * u * u * t * c1[0]
                + 3 * u * t * t * c2[0]
                + t**3 * p1[0],
                u**3 * p0[1]
                + 3 * u * u * t * c1[1]
                + 3 * u * t * t * c2[1]
                + t**3 * p1[1],
            )
        )
    return points
