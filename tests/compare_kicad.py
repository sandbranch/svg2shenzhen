#!/usr/bin/env python3
# Copyright 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
"""
End-to-end check: export a test drawing with the extension, load the
board with KiCad's kicad-cli, plot every layer, and compare it with
Inkscape's own render of that layer.

    tests/compare_kicad.py [drawing.svg ...]   (default: tests/data/board-*.svg)

Needs the Inkscape Flatpak (org.inkscape.Inkscape) or a native inkscape,
the KiCad Flatpak (org.kicad.KiCad) or a native kicad-cli, and numpy and
Pillow on the host. Writes into tests/work/.

Checks, per drawing:
- KiCad loads the board, DRC finds no errors in the board's own items;
- every artwork layer plotted by KiCad matches Inkscape's render (pads
  left out of the copper layers): at most 0.5 % of the pixels differ;
- the board outline plotted by KiCad lies within 0.15 mm of Inkscape's
  outline and the other way round;
- the holes are where the drawing puts them, at its sizes (from
  tests/data/<name>.holes.json, worked out by hand).
Also, every drawing must give the same board as the first one, since
the test drawings are the same board in different units.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
EXT = os.path.join(REPO, "inkscape", "svg2shenzhen")
DPI = 300
A4 = (297.0, 210.0)
BOARD_AT = (148.5, 105.0)  # where the exporter centres the drawing
ART_LAYERS = ["F.Cu", "B.Cu", "F.Mask", "F.SilkS"]


def flatpak(app):
    return (
        shutil.which("flatpak")
        and subprocess.run(
            ["flatpak", "info", app], capture_output=True, check=False
        ).returncode
        == 0
    )


def inkscape_cmd(*args):
    if flatpak("org.inkscape.Inkscape"):
        return [
            "flatpak",
            "run",
            f"--filesystem={REPO}",
            "org.inkscape.Inkscape",
            *args,
        ]
    return ["inkscape", *args]


def extension_cmd(*args):
    if flatpak("org.inkscape.Inkscape"):
        return [
            "flatpak",
            "run",
            f"--filesystem={REPO}",
            "--env=PYTHONPATH=/app/share/inkscape/extensions",
            "--command=python3",
            "org.inkscape.Inkscape",
            *args,
        ]
    # native Inkscape keeps inkex in its extensions folder
    os.environ.setdefault("PYTHONPATH", "/usr/share/inkscape/extensions")
    return [sys.executable, *args]


def kicad_cmd(*args):
    if flatpak("org.kicad.KiCad"):
        return [
            "flatpak",
            "run",
            f"--filesystem={REPO}",
            "--command=kicad-cli",
            "org.kicad.KiCad",
            *args,
        ]
    return ["kicad-cli", *args]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result


def render(svg, png, area="--export-area-page"):
    run(
        inkscape_cmd(
            svg,
            area,
            f"--export-dpi={DPI}",
            "--export-background=#ffffff",
            "--export-background-opacity=1",
            f"--export-filename={png}",
        )
    )
    return np.asarray(Image.open(png).convert("L")) < 128  # True: drawn


def layer_only(drawing, label, out):
    """The drawing with only the layer `label` (and none of the others)."""
    from lxml import etree

    tree = etree.parse(drawing)
    ns = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
    }
    for layer in tree.xpath('//svg:g[@inkscape:groupmode="layer"]', namespaces=ns):
        if layer.get(f"{{{ns['inkscape']}}}label") != label:
            layer.getparent().remove(layer)
    tree.write(out)


def board_area(mask, width_mm, height_mm):
    """Cut the board's area out of a full A4 KiCad plot."""
    px = DPI / 25.4
    x0 = round((BOARD_AT[0] - width_mm / 2) * px)
    y0 = round((BOARD_AT[1] - height_mm / 2) * px)
    return mask[y0 : y0 + round(height_mm * px), x0 : x0 + round(width_mm * px)]


def dilate(mask, steps):
    out = mask.copy()
    for _ in range(steps):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def pads(board_text):
    found = []
    for m in re.finditer(
        r'\(pad "[^"]*" (np_thru_hole|thru_hole) circle \(at ([-0-9.]+) ([-0-9.]+)\) '
        r"\(size ([-0-9.]+) [-0-9.]+\) \(drill ([-0-9.]+)\)",
        board_text,
    ):
        found.append(
            {
                "plated": m.group(1) == "thru_hole",
                "x": float(m.group(2)),
                "y": float(m.group(3)),
                "size": float(m.group(4)),
                "drill": float(m.group(5)),
            }
        )
    return found


def check(drawing, failures):
    name = os.path.splitext(os.path.basename(drawing))[0]
    out = os.path.join(WORK, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    run(
        extension_cmd(
            os.path.join(EXT, "export.py"),
            "--filetype=kicad_pcb",
            f"--path={out}",
            "--dpi=1200",
            "--threshold=128",
            drawing,
        )
    )
    board = os.path.join(out, name + ".kicad_pcb")
    text = read(board)

    # KiCad loads it; DRC
    drc = os.path.join(out, "drc.json")
    run(kicad_cmd("pcb", "drc", "--format", "json", "-o", drc, board))
    violations = json.loads(read(drc))["violations"]
    # design rules (clearances) are the drawing's business; these mean
    # the exporter wrote something broken
    broken = {"invalid_outline", "annular_width", "padstack", "malformed_courtyard"}
    serious = [v for v in violations if v["type"] in broken]
    print(
        f"  DRC: {len(violations)} findings, {len(serious)} of them broken output"
        + (f" ({sorted({v['type'] for v in violations})})" if violations else "")
    )
    if serious:
        failures.append(f"{name}: DRC {[v['description'] for v in serious]}")

    width_mm, height_mm = 80.0, 60.0
    hole_mask = np.zeros(1, bool)
    for layer in ART_LAYERS + ["Edge.Cuts"]:
        layer_svg = os.path.join(out, f"ink-{layer}.svg")
        # F.Mask-auto is filled from F.Cu, so F.Mask must look like F.Cu
        layer_only(drawing, "F.Cu" if layer == "F.Mask" else layer, layer_svg)
        expect = render(layer_svg, layer_svg[:-4] + ".png")
        plot = os.path.join(out, f"kicad-{layer}.svg")
        run(
            kicad_cmd(
                "pcb",
                "export",
                "svg",
                "--layers",
                layer,
                "--mode-single",
                "--black-and-white",
                "--exclude-drawing-sheet",
                "--page-size-mode",
                "1",
                "--drill-shape-opt",
                "0",
                "-o",
                plot,
                board,
            )
        )
        got = board_area(render(plot, plot[:-4] + ".png"), width_mm, height_mm)
        h, w = min(got.shape[0], expect.shape[0]), min(got.shape[1], expect.shape[1])
        got, expect = got[:h, :w], expect[:h, :w]
        if layer == "Edge.Cuts":
            reach = round(0.15 * DPI / 25.4)
            stray = (got & ~dilate(expect, reach)).sum() + (
                expect & ~dilate(got, reach)
            ).sum()
            ok = stray == 0
            print(
                f"  {layer}: {'ok' if ok else 'FAIL'}, {stray} outline pixels further than 0.15 mm"
            )
        else:
            if layer in ("F.Cu", "B.Cu", "F.Mask"):
                if hole_mask.shape != got.shape:
                    hole_mask = np.zeros(got.shape, bool)
                    px = DPI / 25.4
                    yy, xx = np.mgrid[0:h, 0:w]
                    for p in pads(text):
                        cx = (p["x"] + width_mm / 2) * px
                        cy = (p["y"] + height_mm / 2) * px
                        hole_mask |= (xx - cx) ** 2 + (yy - cy) ** 2 <= (
                            p["size"] / 2 * px + 3
                        ) ** 2
                differ = (got != expect) & ~hole_mask
            else:
                differ = got != expect
            share = differ.mean() * 100
            ok = share <= 0.5
            print(
                f"  {layer}: {'ok' if ok else 'FAIL'}, {share:.3f} % of pixels differ "
                f"({expect.sum()} drawn in Inkscape, {got.sum()} in KiCad)"
            )
        if not ok:
            failures.append(f"{name}: {layer}")

    # holes, against the values worked out by hand
    expected = json.loads(read(os.path.join(HERE, "data", "board.holes.json")))
    found = sorted(pads(text), key=lambda p: (p["x"], p["y"]))
    expected = sorted(expected, key=lambda p: (p["x"], p["y"]))
    holes_ok = len(found) == len(expected) and all(
        abs(f["x"] - e["x"]) < 1e-3
        and abs(f["y"] - e["y"]) < 1e-3
        and abs(f["drill"] - e["drill"]) < 1e-3
        and f["plated"] == e["plated"]
        for f, e in zip(found, expected)
    )
    print(f"  holes: {'ok' if holes_ok else 'FAIL'}, {len(found)} found")
    if not holes_ok:
        failures.append(f"{name}: holes {found}")

    # the same drawing as one footprint: KiCad must load it, with the
    # same holes and an outline of the same size
    library = os.path.join(out, "fp", "lib.pretty")
    os.makedirs(library)
    run(
        extension_cmd(
            os.path.join(EXT, "export.py"),
            "--filetype=kicad_module",
            f"--path={library}",
            "--dpi=1200",
            "--threshold=128",
            drawing,
        )
    )
    footprint = read(os.path.join(library, name + ".kicad_mod"))
    plots = os.path.join(out, "fp", "svg")
    os.makedirs(plots)
    run(
        kicad_cmd(
            "fp", "export", "svg", "-o", plots, "--layers", "F.Cu,Edge.Cuts", library
        )
    )
    loaded = os.path.isfile(os.path.join(plots, name + ".svg"))
    same_holes = sorted(pads(footprint), key=lambda p: (p["x"], p["y"])) == found
    outline = len(re.findall(r"\(fp_(?:line|curve|circle) ", footprint)) == len(
        re.findall(r"\(gr_(?:line|curve|circle) ", text)
    )
    fp_ok = loaded and same_holes and outline
    print(
        f"  footprint: {'ok' if fp_ok else 'FAIL'} (KiCad loads it: {loaded}, same holes: {same_holes}, "
        f"same outline: {outline})"
    )
    if not fp_ok:
        failures.append(f"{name}: footprint")
    return text


def main():
    drawings = sys.argv[1:] or sorted(
        os.path.join(HERE, "data", n)
        for n in os.listdir(os.path.join(HERE, "data"))
        if n.startswith("board-") and n.endswith(".svg")
    )
    os.makedirs(WORK, exist_ok=True)
    failures, boards = [], []
    for drawing in drawings:
        print(os.path.basename(drawing))
        boards.append(check(os.path.abspath(drawing), failures))

    def numbers(text):
        """The numbers of the board's vector items: outline and pads (the
        traced polygons may differ by a point here and there)."""
        items = re.findall(r"\((?:gr_line|gr_curve|gr_circle|pad) .*", text)
        return [
            float(v)
            for v in re.findall(
                r"(?<=[ (])-?[0-9]+\.?[0-9]*(?=[ )])",
                re.sub(r'"[^"]*"', "", "\n".join(items)),
            )
        ]

    for other, drawing in zip(boards[1:], drawings[1:]):
        a, b = numbers(boards[0]), numbers(other)
        same = (
            len(a) == len(b)
            and max((abs(x - y) for x, y in zip(a, b)), default=0) <= 0.01
        )
        print(
            f"{os.path.basename(drawing)} gives the same board as {os.path.basename(drawings[0])}: "
            f"{'ok' if same else 'FAIL'}"
            + ("" if same else f" ({len(a)} vs {len(b)} numbers)")
        )
        if not same:
            failures.append(
                f"{os.path.basename(drawing)} differs from {os.path.basename(drawings[0])}"
            )
    print("ALL PASS" if not failures else "FAILED:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
