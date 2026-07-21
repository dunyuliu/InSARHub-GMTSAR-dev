"""
Verify InSARHub imports and registers its analyzers without MintPy/pyaps3/GDAL
installed.

MintPy was made an optional dependency (`insarhub[mintpy]`) — analyzer.mintpy_base
/hyp3_sbas/isce_sbas no longer import mintpy/pyaps3/osgeo at module level, only
inside the methods that actually need them. This test simulates their absence
(by blocking the imports and forcing a fresh import of the insarhub module
tree) and confirms `insarhub.app.state` still loads and the MintPy-backed
analyzers still register normally.

Does NOT require ISCE2, MintPy, GDAL, or SLURM.
"""

from __future__ import annotations

import sys
import unittest

_BLOCKED = ("mintpy", "pyaps3", "osgeo")


def _insarhub_module_names() -> list[str]:
    return [n for n in sys.modules if n == "insarhub" or n.startswith("insarhub.")]


def _blocked_module_names() -> list[str]:
    return [
        n for n in sys.modules
        if n in _BLOCKED or any(n.startswith(f"{b}.") for b in _BLOCKED)
    ]


class TestMintpyOptional(unittest.TestCase):

    def setUp(self):
        self._saved: dict = {}
        for name in _insarhub_module_names() + _blocked_module_names():
            self._saved[name] = sys.modules.pop(name, None)
        # Setting a module to None in sys.modules makes `import <name>` raise
        # ImportError immediately, simulating the package not being installed.
        for name in _BLOCKED:
            sys.modules[name] = None

    def tearDown(self):
        for name in _insarhub_module_names() + _blocked_module_names():
            sys.modules.pop(name, None)
        for name, mod in self._saved.items():
            if mod is not None:
                sys.modules[name] = mod

    def test_mintpy_import_is_actually_blocked(self):
        """Sanity check: confirm this test exercises the 'mintpy absent' case."""
        with self.assertRaises(ImportError):
            import mintpy  # noqa: F401

    def test_state_imports_without_mintpy(self):
        import insarhub.app.state as state
        names = state.Analyzer.available()
        self.assertIn("Hyp3_SBAS", names)
        self.assertIn("ISCE_SBAS", names)
        # Processor/Downloader registries are unaffected by this change.
        self.assertIn("ISCE_S1", state.Processor.available())
        self.assertIn("Hyp3_S1", state.Processor.available())

    def test_config_still_has_container_field(self):
        import dataclasses

        import insarhub.app.state as state
        cfg_cls = state.Analyzer._registry["Hyp3_SBAS"].default_config
        field_names = {f.name for f in dataclasses.fields(cfg_cls)}
        self.assertIn("container", field_names)


if __name__ == "__main__":
    unittest.main()
