# Changelog

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
