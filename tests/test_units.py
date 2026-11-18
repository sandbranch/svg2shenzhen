# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Unit tests for geometry.py and kicad.py (no Inkscape or KiCad needed;
inkex 1.4 is: pip install --no-deps inkex==1.4.0, plus lxml, numpy,
tinycss2, cssselect, pyparsing, packaging).

    python3 -m pytest tests/test_units.py
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "inkscape", "svg2shenzhen"))

import geometry
import inkex
import kicad


def svg(body, size='width="80mm" height="60mm" viewBox="0 0 80 60"'):
    return inkex.load_svg(
        f'<svg xmlns="http://www.w3.org/2000/svg" {size}>'
        f'<g id="L" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'inkscape:groupmode="layer">{body}</g></svg>'
    ).getroot()


def test_mapping_mm_document():
    m = geometry.Mapping(svg(""))
    assert m.point(40, 30) == pytest.approx((0, 0))
    assert m.point(0, 0) == pytest.approx((-40, -30))
    assert m.length(10) == pytest.approx(10)


def test_mapping_scaled_offset_document():
    # user unit 0.5 mm, viewBox starting at (-10, -20)
    m = geometry.Mapping(
        svg("", 'width="80mm" height="60mm" viewBox="-10 -20 160 120"')
    )
    assert m.point(70, 40) == pytest.approx((0, 0))  # the page centre
    assert m.point(-10, -20) == pytest.approx((-40, -30))
    assert m.length(10) == pytest.approx(5)


def test_mapping_px_document():
    m = geometry.Mapping(svg("", 'width="300" height="150" viewBox="0 0 300 150"'))
    assert m.length(96) == pytest.approx(25.4)


def test_circle_through_transforms():
    root = svg(
        '<g transform="translate(10 5) scale(2)"><circle id="c" cx="3" cy="4" r="1.5"/></g>'
    )
    cx, cy, r = geometry.circle_of(root.getElementById("c"))
    assert (cx, cy, r) == pytest.approx((16, 13, 3))


def test_squashed_circle_is_not_a_circle():
    root = svg('<g transform="scale(2 1)"><circle id="c" cx="3" cy="4" r="1"/></g>')
    assert geometry.circle_of(root.getElementById("c")) is None
    root = svg('<ellipse id="e" cx="3" cy="4" rx="1" ry="2"/>')
    assert geometry.circle_of(root.getElementById("e")) is None


def test_rounded_rect_outline():
    root = svg('<rect id="r" x="5" y="5" width="70" height="50" rx="4"/>')
    (segments,) = geometry.subpaths(root.getElementById("r"))
    kinds = [s[0] for s in segments]
    # four sides; each rounded corner is one or more Bezier pieces
    assert kinds.count("line") == 4 and kinds.count("curve") >= 4
    # closed: the last segment ends where the first starts
    assert segments[-1][-1] == pytest.approx(segments[0][1])


def test_subpaths_stay_apart():
    root = svg('<path id="p" d="M 0,0 h 10 v 10 z M 20,20 h 5 v 5 z"/>')
    subs = geometry.subpaths(root.getElementById("p"))
    assert len(subs) == 2
    assert all(seg[0] == "line" for sub in subs for seg in sub)


def test_hidden_shapes_are_skipped():
    root = svg(
        '<rect id="a" width="1" height="1"/><g style="display:none"><rect id="b" width="1" height="1"/></g>'
    )
    layer = root.getElementById("L")
    assert [n.get("id") for n in geometry.shapes(layer)] == ["a"]


def test_flatten_stays_on_the_curve():
    seg = ("curve", (0, 0), (0, 10), (10, 10), (10, 0))
    points = geometry.flatten(seg, 0.01)
    assert points[0] == (0, 0) and points[-1] == pytest.approx((10, 0))
    assert len(points) > 10


def test_numbers():
    assert kicad.num(1.0) == "1"
    assert kicad.num(-0.0000001) == "0"
    assert kicad.num(2.5) == "2.5"
    assert kicad.num(1 / 3) == "0.333333"


def test_parse_traced():
    text = """(module LOGO (layer F.Cu)
  (fp_poly (pts (xy 1.0 2.0) (xy 3 4) (xy 5 -6.5)
  ) (layer F.Cu) (width  0.010000)
 )
  (fp_poly (pts (xy 1 1) (xy 2 2)) (layer F.SilkS) (width 0.01))
)"""
    assert kicad.parse_traced(text) == [("F.Cu", [(1.0, 2.0), (3.0, 4.0), (5.0, -6.5)])]


def test_board_and_footprint_text():
    polygons = [("F.Cu", [(0, 0), (1, 0), (1, 1)])]
    holes = [(2, 3, 1.0, True), (4, 5, 2.0, False)]
    edges = [
        ("line", (-5, -5), (5, -5)),
        ("curve", (5, -5), (6, 0), (6, 0), (5, 5)),
        ("circle", 0, 0, 2),
    ]
    board = kicad.board_file("t", polygons, holes, edges, ring=0.25)
    assert board.startswith("(kicad_pcb\n\t(version 20241229)")
    assert '(pad "1" thru_hole circle (at 2 3) (size 1.5 1.5) (drill 1)' in board
    assert '(pad "" np_thru_hole circle (at 4 5) (size 2 2) (drill 2)' in board
    # the outline is on the board, moved to the sheet centre (148.5, 105)
    assert "(gr_line (start 143.5 100) (end 153.5 100)" in board
    assert "(gr_circle (center 148.5 105) (end 150.5 105)" in board
    assert board == kicad.board_file(
        "t", polygons, holes, edges, ring=0.25
    )  # repeatable
    footprint = kicad.footprint_file("t", polygons, holes, edges)
    assert footprint.startswith('(footprint "t"\n\t(version 20241229)')
    assert "(fp_curve (pts (xy 5 -5) (xy 6 0) (xy 6 0) (xy 5 5))" in footprint


def test_library_table():
    assert '(uri "${KIPRJMOD}/t.pretty")' in kicad.library_table("t")
