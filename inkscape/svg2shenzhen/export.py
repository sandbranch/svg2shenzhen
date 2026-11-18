#!/usr/bin/env python3
# Copyright (C) 2018-2025 Budi Prakosa (badgeek) and svg2shenzhen contributors
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
Export a layered drawing to KiCad.

Artwork layers (copper, mask, silkscreen and the other KiCad layers) are
rendered by Inkscape and traced into filled polygons by bitmap2component,
so any drawing works: text, strokes, gradients. The board outline
(Edge.Cuts) and the holes (Drill, Drill.NPTH) are read as vectors, with
all transforms and in real millimetres, and written as lines, curves and
circles, and as pads.

Layer names follow KiCad's (F.Cu, B.SilkS, ...). "-auto" on a mask layer
fills it from the copper layer; "-invert" inverts a layer; "-disabled"
skips it; layers named "[fixed] ..." are drawn under every layer.
"""

import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from copy import deepcopy

import inkex
from inkex.base import TempDirMixin
from inkex.command import ProgramRunError, inkscape
from inkex.localization import inkex_gettext as _

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geometry
import kicad

PARALLEL = 3  # Inkscape and bitmap2component runs at a time

# Inkscape layer name -> KiCad layer name
LAYER_MAP = {name: name for name in kicad.LAYER_NAMES if name != "Edge.Cuts"}
LAYER_MAP.update({"B.Silk": "B.SilkS", "F.Silk": "F.SilkS"})  # older drawings

# layers bitmap2component knows; others are traced as F.SilkS and renamed
TRACER_LAYERS = {
    "F.Cu",
    "B.Cu",
    "F.SilkS",
    "B.SilkS",
    "F.Mask",
    "B.Mask",
    "Eco1.User",
    "Eco2.User",
}


def layer_label(layer):
    return layer.get("inkscape:label") or ""


def tracer_path():
    folder = os.path.dirname(os.path.abspath(__file__))
    system = platform.system()
    if system == "Darwin":
        names = ["bitmap2component_osx"]
    elif system == "Windows":
        names = ["bitmap2component.exe"]
    else:
        names = ["bitmap2component_linux64", "bitmap2component"]
    for name in names:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return shutil.which("bitmap2component")


class Svg2ShenzhenExport(TempDirMixin, inkex.EffectExtension):
    """Export the drawing's layers to a KiCad footprint or board"""

    def add_arguments(self, pars):
        pars.add_argument("--path", default="")
        pars.add_argument("-f", "--filetype", default="kicad_module")
        pars.add_argument("--crop", type=inkex.Boolean, default=False)
        pars.add_argument("--dpi", type=int, default=1200)
        pars.add_argument("--threshold", type=float, default=128.0)
        pars.add_argument("--ring", type=float, default=0.25, help="annular ring, mm")
        pars.add_argument("--openfactory", type=inkex.Boolean, default=False)
        pars.add_argument("--openkicad", type=inkex.Boolean, default=False)
        pars.add_argument(
            "--autoflatten", type=inkex.Boolean, default=False
        )  # unused now
        pars.add_argument("--debug", type=inkex.Boolean, default=False)

    # -- where and what

    def output_folder(self):
        path = os.path.expanduser(str(self.options.path or "").strip())
        if not path:
            # the drawing's own folder, else the home folder
            path = self.svg_path() or os.path.expanduser("~")
        os.makedirs(path, exist_ok=True)
        return path

    def name(self):
        docname = self.svg.get("sodipodi:docname") or self.document_path() or "board"
        return os.path.splitext(os.path.basename(docname))[0] or "board"

    def find_layer(self, name):
        for layer in self.svg.xpath('//svg:g[@inkscape:groupmode="layer"]'):
            if layer_label(layer) == name:
                return layer
        return None

    def artwork_layers(self):
        """(layer, kicad layer, invert) for every layer to trace."""
        found = []
        for layer in self.svg.xpath('//svg:g[@inkscape:groupmode="layer"]'):
            label = layer_label(layer)
            if "-disabled" in label or label.lower().startswith("[fixed]"):
                continue
            invert = "-invert" in label
            base = label.replace("-invert", "").replace("-auto", "")
            if base in LAYER_MAP:
                found.append((layer, LAYER_MAP[base], invert))
        return found

    def fill_auto_masks(self):
        """Copy the copper drawing into F.Mask-auto / B.Mask-auto."""
        for side in ("F", "B"):
            copper = self.find_layer(f"{side}.Cu")
            for layer in self.svg.xpath('//svg:g[@inkscape:groupmode="layer"]'):
                label = layer_label(layer)
                if (
                    label.startswith(f"{side}.Mask")
                    and "-auto" in label
                    and copper is not None
                ):
                    for child in list(layer):
                        layer.remove(child)
                    for child in copper:
                        layer.append(deepcopy(child))

    # -- vectors: Edge.Cuts and drills

    def edges(self, mapping):
        layer = self.find_layer("Edge.Cuts")
        if layer is None:
            return []
        items = []
        for node in geometry.shapes(layer):
            circle = geometry.circle_of(node)
            if circle is not None:
                cx, cy = mapping.point(circle[0], circle[1])
                items.append(("circle", cx, cy, mapping.length(circle[2])))
                continue
            for sub in geometry.subpaths(node):
                for segment in sub:
                    points = [mapping.point(*p) for p in segment[1:]]
                    items.append((segment[0], *points))
        return items

    def drills(self, mapping):
        found, odd = [], []
        for name, plated in (("Drill", True), ("Drill.NPTH", False)):
            layer = self.find_layer(name)
            if layer is None:
                continue
            for node in geometry.shapes(layer):
                circle = geometry.circle_of(node)
                if circle is None:
                    odd.append(node.get("id"))
                    continue
                x, y = mapping.point(circle[0], circle[1])
                diameter = mapping.length(2 * circle[2])
                found.append((x, y, diameter, plated))
        if odd:
            inkex.errormsg(
                _("Holes must be circles; skipped in the drill layers: {}").format(
                    ", ".join(odd)
                )
            )
        return found

    # -- artwork: render and trace

    def layer_document(self, keep):
        """A copy of the drawing with only the layer `keep` and the
        [fixed] layers, all visible."""
        doc = deepcopy(self.document)
        root = doc.getroot()
        for layer in root.xpath('//svg:g[@inkscape:groupmode="layer"]'):
            label = layer_label(layer)
            if layer.get("id") == keep or label.lower().startswith("[fixed]"):
                layer.style["display"] = "inline"
            elif layer.getparent() is not None:
                layer.getparent().remove(layer)
        return doc

    def run_all(self, commands):
        """Run commands, PARALLEL at a time; return their (code, output)."""
        results = []
        for i in range(0, len(commands), PARALLEL):
            batch = [
                subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                for cmd in commands[i : i + PARALLEL]
            ]
            for process in batch:
                out, _err = process.communicate()
                results.append((process.returncode, out.decode("utf-8", "replace")))
        return results

    def render(self, layers, folder):
        """Render every layer in one Inkscape run (several Inkscape
        processes at once collide on D-Bus and render nothing)."""
        area = "export-area-drawing" if self.options.crop else "export-area-page"
        files, actions, pngs = [], [], []
        for layer, kicad_layer, _invert in layers:
            stem = f"{kicad_layer}_{layer.get('id')}"
            svg_file = os.path.join(self.tempdir, stem + ".svg")
            self.layer_document(layer.get("id")).write(svg_file)
            png = os.path.join(folder, stem + ".png")
            if files:
                actions += ["file-close", "file-open:" + svg_file]
            files.append(svg_file)
            pngs.append(png)
            actions += [
                area,
                f"export-dpi:{self.options.dpi}",
                "export-background:#ffffff",
                "export-background-opacity:1",
                "export-filename:" + png,
                "export-do",
            ]
        try:
            inkscape(files[0], actions=";".join(actions))
        except ProgramRunError as error:
            raise inkex.AbortExtension(
                _("Inkscape could not render the layers:\n{}").format(error)
            ) from error
        missing = [png for png in pngs if not os.path.isfile(png)]
        if missing:
            raise inkex.AbortExtension(
                _("Inkscape rendered nothing for: {}").format(", ".join(missing))
            )
        return pngs

    def trace(self, layers, pngs):
        tracer = tracer_path()
        if not tracer:
            raise inkex.AbortExtension(
                _(
                    "bitmap2component was not found next to the extension; reinstall svg2shenzhen."
                )
            )
        jobs, outputs = [], []
        for (layer, kicad_layer, invert), png in zip(layers, pngs):
            out = os.path.join(self.tempdir, os.path.basename(png)[:-4] + ".kicad_mod")
            outputs.append(out)
            traced_as = kicad_layer if kicad_layer in TRACER_LAYERS else "F.SilkS"
            jobs.append(
                [
                    tracer,
                    png,
                    out,
                    traced_as,
                    "false" if invert else "true",
                    str(int(self.options.dpi)),
                    str(int(self.options.threshold)),
                ]
            )
        polygons = []
        for (code, log), out, (_layer, kicad_layer, _inv) in zip(
            self.run_all(jobs), outputs, layers
        ):
            if code != 0 or not os.path.isfile(out):
                raise inkex.AbortExtension(
                    _("Tracing failed for {}:\n{}").format(kicad_layer, log)
                )
            with open(out, encoding="utf-8", errors="replace") as f:
                for _traced, points in kicad.parse_traced(f.read()):
                    polygons.append((kicad_layer, points))
        return polygons

    # -- the export

    def effect(self):
        options = self.options
        folder = self.output_folder()
        name = self.name()
        mapping = geometry.Mapping(self.svg)

        self.fill_auto_masks()
        layers = self.artwork_layers()

        if options.filetype == "png":
            self.render(layers, folder)
            return

        image_folder = os.path.join(self.tempdir, "images")
        os.makedirs(image_folder, exist_ok=True)
        polygons = (
            self.trace(layers, self.render(layers, image_folder)) if layers else []
        )
        holes = self.drills(mapping)
        edges = self.edges(mapping)
        ring = options.ring

        if options.filetype == "kicad_pcb":
            library = os.path.join(folder, name + ".pretty")
            os.makedirs(library, exist_ok=True)
            footprint = kicad.footprint_file(name, polygons, holes, [], ring)
            with open(
                os.path.join(library, name + ".kicad_mod"), "w", encoding="utf-8"
            ) as f:
                f.write(footprint)
            board = os.path.join(folder, name + ".kicad_pcb")
            with open(board, "w", encoding="utf-8") as f:
                f.write(kicad.board_file(name, polygons, holes, edges, ring))
            with open(
                os.path.join(folder, name + ".kicad_pro"), "w", encoding="utf-8"
            ) as f:
                f.write(kicad.project_file(name))
            with open(os.path.join(folder, "fp-lib-table"), "w", encoding="utf-8") as f:
                f.write(kicad.library_table(name))
            if options.openkicad:
                self.open_file(board)
        else:
            path = os.path.join(folder, name + ".kicad_mod")
            with open(path, "w", encoding="utf-8") as f:
                f.write(kicad.footprint_file(name, polygons, holes, edges, ring))

        if options.openfactory:
            webbrowser.open(
                "https://www.pcbway.com/setinvite.aspx?inviteid=54747", new=2
            )

    def open_file(self, path):
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def save_raw(self, ret):
        """The drawing itself is not changed (auto masks are filled in a
        copy only), so nothing is written back."""


if __name__ == "__main__":
    Svg2ShenzhenExport().run()
