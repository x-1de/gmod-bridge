# Third-party software and assets

The MIT license applies to GMod Bridge source code. It does not grant rights to
Garry's Mod, Valve SDK tools, Workshop addons, models, maps or textures.
These are not included. Users supply their own legally obtained game and assets.
Publishing a converted asset requires the appropriate permission from its owner.

Blender is a separate application and is not bundled (GPL, https://www.blender.org/about/license/).
GMod tools (`studiomdl`, `gmad`) are run from the user's game installation and not redistributed.

The desktop distribution includes dependencies with their own licenses:

* PySide6 / Qt: LGPLv3/GPLv3 or commercial; https://doc.qt.io/qtforpython-6/licenses.html
* Pillow: HPND; https://github.com/python-pillow/Pillow/blob/main/LICENSE
* NumPy: BSD-3-Clause; https://numpy.org/doc/stable/license.html
* Python: PSF; https://docs.python.org/3/license.html
* PyInstaller runtime: GPL with bootloader exception; https://pyinstaller.org/en/stable/license.html

The portable build uses a folder distribution with shared libraries. Preserve
bundled license and notice files when redistributing. Audit the actual dependency
versions and license obligations before distributing a modified binary.
