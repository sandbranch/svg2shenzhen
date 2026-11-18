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
Write KiCad boards and footprints, in the file format of KiCad 9
(version 20241229), which KiCad 9 and 10 open. The syntax follows the
files KiCad 9 itself writes (its bundled demo projects).

UUIDs are derived from the content, so the same drawing always gives
the same files.
"""

import json
import re
import uuid

VERSION = 20241229
GENERATOR = "svg2shenzhen"
NAMESPACE = uuid.UUID("7c1d9e8e-5b0a-4f0e-9a43-2f9f5b1a6c11")

# KiCad 9 board layer numbers and names, as KiCad 9 writes them
BOARD_LAYERS = [
    (0, "F.Cu", "signal", "top_cu"),
    (2, "B.Cu", "signal", "bottom_cu"),
    (9, "F.Adhes", "user", "F.Adhesive"),
    (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user", None),
    (15, "B.Paste", "user", None),
    (5, "F.SilkS", "user", "F.Silkscreen"),
    (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user", None),
    (3, "B.Mask", "user", None),
    (17, "Dwgs.User", "user", "User.Drawings"),
    (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"),
    (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user", None),
    (27, "Margin", "user", None),
    (31, "F.CrtYd", "user", "F.Courtyard"),
    (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user", None),
    (33, "B.Fab", "user", None),
]
LAYER_NAMES = {name for _n, name, _t, _u in BOARD_LAYERS}


def num(value):
    """A number as KiCad writes it: up to 6 decimals, no trailing zeros."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def xy(point):
    return f"{num(point[0])} {num(point[1])}"


def quote(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Ids:
    """Repeatable UUIDs: the n-th one for a given name is always the same."""

    def __init__(self, name):
        self.name = name
        self.count = 0

    def __call__(self):
        self.count += 1
        return str(uuid.uuid5(NAMESPACE, f"{self.name}/{self.count}"))


def parse_traced(text):
    """The polygons (layer, [points]) in a footprint written by
    bitmap2component, in millimetres around the image centre."""
    polygons = []
    for match in re.finditer(
        r"\(fp_poly\s*\(pts(.*?)\)\s*\(layer\s+([^)\s]+)\)", text, re.DOTALL
    ):
        points = [
            (float(x), float(y))
            for x, y in re.findall(
                r"\(xy\s+([-0-9.eE+]+)\s+([-0-9.eE+]+)\)", match.group(1)
            )
        ]
        if len(points) >= 3:
            polygons.append((match.group(2), points))
    return polygons


def _stroke(width):
    return f"(stroke (width {num(width)}) (type solid))"


def edge_items(edges, prefix, ids, width=0.1):
    """Edge.Cuts lines, curves and circles; prefix 'gr' on a board, 'fp'
    in a footprint."""
    out = []
    for item in edges:
        kind = item[0]
        if kind == "line":
            body = f"(start {xy(item[1])}) (end {xy(item[2])})"
            out.append(
                f'({prefix}_line {body} {_stroke(width)} (layer "Edge.Cuts") (uuid {quote(ids())}))'
            )
        elif kind == "curve":
            pts = " ".join(f"(xy {xy(p)})" for p in item[1:])
            out.append(
                f'({prefix}_curve (pts {pts}) {_stroke(width)} (layer "Edge.Cuts") (uuid {quote(ids())}))'
            )
        elif kind == "circle":
            cx, cy, r = item[1], item[2], item[3]
            body = f"(center {xy((cx, cy))}) (end {xy((cx + r, cy))})"
            out.append(
                f'({prefix}_circle {body} {_stroke(width)} (fill no) (layer "Edge.Cuts") (uuid {quote(ids())}))'
            )
    return out


def footprint_items(polygons, holes, ids, ring=0.25):
    """fp_poly for the traced artwork; pads for the holes (x, y,
    diameter, plated), plated ones with a copper ring `ring` mm wide."""
    out = []
    for layer, points in polygons:
        pts = " ".join(f"(xy {xy(p)})" for p in points)
        out.append(
            f"(fp_poly (pts {pts}) {_stroke(0)} (fill yes) (layer {quote(layer)}) (uuid {quote(ids())}))"
        )
    number = 0
    for x, y, diameter, plated in holes:
        if plated:
            number += 1
            size = diameter + 2 * ring
            out.append(
                f"(pad {quote(number)} thru_hole circle (at {xy((x, y))}) (size {num(size)} {num(size)}) "
                f'(drill {num(diameter)}) (layers "*.Cu" "*.Mask") (uuid {quote(ids())}))'
            )
        else:
            out.append(
                f'(pad "" np_thru_hole circle (at {xy((x, y))}) (size {num(diameter)} {num(diameter)}) '
                f'(drill {num(diameter)}) (layers "*.Cu" "*.Mask") (uuid {quote(ids())}))'
            )
    return out


def _properties(name, ids, at="0 0"):
    hidden = "(hide yes)"
    font = "(effects (font (size 1 1) (thickness 0.15)))"
    return [
        f'(property "Reference" "G1" (at {at}) (layer "F.SilkS") {hidden} (uuid {quote(ids())}) {font})',
        f'(property "Value" {quote(name)} (at {at}) (layer "F.Fab") {hidden} (uuid {quote(ids())}) {font})',
    ]


def footprint_file(name, polygons, holes, edges, ring=0.25):
    """A .kicad_mod with everything in one footprint."""
    ids = Ids(f"footprint/{name}")
    lines = [
        f"(footprint {quote(name)}",
        f"\t(version {VERSION})",
        f"\t(generator {quote(GENERATOR)})",
        '\t(layer "F.Cu")',
        "\t(attr board_only exclude_from_pos_files exclude_from_bom)",
    ]
    lines += ["\t" + item for item in _properties(name, ids)]
    lines += ["\t" + item for item in footprint_items(polygons, holes, ids, ring)]
    lines += ["\t" + item for item in edge_items(edges, "fp", ids)]
    lines.append(")")
    return "\n".join(lines) + "\n"


def board_file(name, polygons, holes, edges, ring=0.25, at=(148.5, 105.0)):
    """A .kicad_pcb: the artwork and drills as one footprint, the board
    outline on Edge.Cuts; centred on an A4 sheet."""
    ids = Ids(f"board/{name}")
    lines = [
        "(kicad_pcb",
        f"\t(version {VERSION})",
        f"\t(generator {quote(GENERATOR)})",
        "\t(general (thickness 1.6) (legacy_teardrops no))",
        '\t(paper "A4")',
        "\t(layers",
    ]
    for number, layer, kind, user in BOARD_LAYERS:
        user_name = f" {quote(user)}" if user else ""
        lines.append(f"\t\t({number} {quote(layer)} {kind}{user_name})")
    lines += [
        "\t)",
        "\t(setup (pad_to_mask_clearance 0) (allow_soldermask_bridges_in_footprints no))",
        '\t(net 0 "")',
    ]
    if polygons or holes:
        lines.append(f"\t(footprint {quote('svg2shenzhen:' + name)}")
        lines.append('\t\t(layer "F.Cu")')
        lines.append(f"\t\t(uuid {quote(ids())})")
        lines.append(f"\t\t(at {xy(at)})")
        lines += ["\t\t" + item for item in _properties(name, ids)]
        lines.append("\t\t(attr board_only exclude_from_pos_files exclude_from_bom)")
        lines += ["\t\t" + item for item in footprint_items(polygons, holes, ids, ring)]
        lines.append("\t)")
    moved = []
    for item in edges:
        if item[0] == "circle":
            moved.append(("circle", item[1] + at[0], item[2] + at[1], item[3]))
        else:
            moved.append((item[0], *[(p[0] + at[0], p[1] + at[1]) for p in item[1:]]))
    lines += ["\t" + item for item in edge_items(moved, "gr", ids)]
    lines.append(")")
    return "\n".join(lines) + "\n"


def project_file(name):
    """A minimal .kicad_pro so KiCad opens the board as a project."""
    return (
        json.dumps({"meta": {"filename": name + ".kicad_pro", "version": 1}}, indent=2)
        + "\n"
    )


def library_table(name):
    """fp-lib-table naming the project's footprint library."""
    return (
        "(fp_lib_table\n\t(version 7)\n"
        f'\t(lib (name "svg2shenzhen")(type "KiCad")(uri "${{KIPRJMOD}}/{name}.pretty")'
        '(options "")(descr "svg2shenzhen export"))\n)\n'
    )
