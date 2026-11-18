#!/usr/bin/env python3
# Copyright (C) 2026 David
# SPDX-License-Identifier: GPL-3.0-or-later
"""The About entry only shows its dialog; there is nothing to do."""

import inkex


class About(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--active-tab")  # the dialog's notebook

    def effect(self):
        pass


if __name__ == "__main__":
    About().run()
