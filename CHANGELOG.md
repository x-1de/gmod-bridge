# Changelog

## Unreleased

* Mark character creation as BETA in navigation and throughout the wizard.
* Fit all five finger chains to individual 3D tips; add six fingertip markers (27 total).
* Optional 64-bone skeleton with eight palm bones; preserve the original ValveBiped animation hierarchy.
* Keep manually placed torso depth; validate missing, invalid and overlapping joint markers.
* Respect palm roll from 3D thumb markers, including rotations greater than 60 degrees.
* Use adjacent bones for fallback weights and handle empty weight proxies / failed heat weighting.
* Recognize coincident UV seam vertices when classifying detached head accessories.
* Scroll wizard settings on smaller windows; add character regression checks for Python and Blender.

## 0.2.0b1

First public beta of GMod Bridge.

* GMA/VPK/folder sources; models and BSP maps to BLEND, FBX, GLB and OBJ.
* Blender face-control addon, skins, bodygroups and flex shape keys.
* Static prop addon compiler and experimental player-model wizard.
* Texture assignment, 21 markers, front/side placement, zoom and keyboard nudging.
* Installable Python package, Windows portable build and GitHub CI.
* Prevent window closure during background work; stage addon installation before replacing it.
* Restrict folder reads to the selected source root; validate export parameters.

Known limits: automated rigging is experimental, map lighting/material conversion
is approximate, no sequence import and no facial rig generation from arbitrary meshes.
