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
    "mintpy.cli",
    "mintpy.cli.geocode",
    "cdsapi",
    # Stubbing "mintpy" above makes `import mintpy` succeed, which makes
    # insarhub/__init__.py's `_has_mintpy` check True and try a real `from
    # dask import config` next — stub dask too so that doesn't fail when
    # dask genuinely isn't installed (it's now an insarhub[mintpy] extra).
    # h5py is NOT stubbed here: it's lazily imported inside h5_to_raster()
    # only, and test_utils_postprocess.py needs the real package to build
    # HDF5 test fixtures.
    "dask",
]

for _name in _STUBS:
    if _name not in sys.modules:
        sys.modules[_name] = _stub(_name)
