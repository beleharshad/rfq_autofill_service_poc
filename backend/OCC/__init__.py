"""Compatibility package for OpenCascade imports.

This project historically imports pythonocc-core modules via `OCC.Core.*`.
Some Linux deployments use the lighter `cadquery-ocp` package instead, which
exposes equivalent modules under `OCP.*`.

The sibling `OCC.Core` package installs an import hook that forwards
`OCC.Core.<module>` requests to `OCP.<module>` when needed.
"""
