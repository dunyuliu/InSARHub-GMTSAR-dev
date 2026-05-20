"""
Shared pytest configuration.

Stubs out heavy optional dependencies (osgeo/gdal, mintpy) before any
insarhub module is imported, so unit tests run without a full ISCE2/GDAL
installation.
"""

import sys
from unittest.mock import MagicMock


def _stub(name: str) -> MagicMock:
    mod = MagicMock(spec=None)
    mod.__path__ = []
    mod.__name__ = name
    mod.__spec__ = None
    mod.__loader__ = None
    mod.__package__ = name
    return mod


_STUBS = [
    "osgeo",
    "osgeo.gdal",
    "osgeo.osr",
    "osgeo.ogr",
    "mintpy",
    "mintpy.smallbaselineApp",
    "mintpy.utils",
    "mintpy.utils.readfile",
    "mintpy.utils.utils",
    "mintpy.utils.network",
    "mintpy.utils.plot",
    "cdsapi",
]

for _name in _STUBS:
    if _name not in sys.modules:
        sys.modules[_name] = _stub(_name)
