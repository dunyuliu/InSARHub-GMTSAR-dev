"""
Tests for insarhub.utils.postprocess — h5_to_raster, save_footprint.

Run: pytest test/test_utils_postprocess.py -v

Uses synthetic HDF5 files (h5py) and synthetic GeoTIFF (rasterio).
No real MintPy outputs required.
"""

import numpy as np
import pytest
from pathlib import Path


# ===========================================================================
# Helpers
# ===========================================================================

def _make_mintpy_h5(path: Path, dataset_name: str, data: np.ndarray,
                    attrs: dict | None = None) -> Path:
    """Write a minimal MintPy-style HDF5 file."""
    import h5py
    default_attrs = {
        "X_FIRST": "-121.0",
        "Y_FIRST": "37.0",
        "X_STEP": "0.001",
        "Y_STEP": "-0.001",
        "X_UNIT": "degrees",
        "Y_UNIT": "degrees",
        "WIDTH": str(data.shape[-1]),
        "LENGTH": str(data.shape[-2] if data.ndim >= 2 else data.shape[0]),
        "EPSG": "4326",
    }
    if attrs:
        default_attrs.update(attrs)
    with h5py.File(path, "w") as f:
        ds = f.create_dataset(dataset_name, data=data)
        for k, v in default_attrs.items():
            f.attrs[k] = v
            ds.attrs[k] = v
    return path


def _make_geotiff(path: Path, data: np.ndarray) -> Path:
    """Write a minimal GeoTIFF."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.crs import CRS
    transform = from_origin(-121.0, 37.0, 0.001, 0.001)
    height, width = data.shape
    with rasterio.open(
        str(path), "w",
        driver="GTiff", height=height, width=width,
        count=1, dtype=data.dtype,
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return path


# ===========================================================================
# h5_to_raster
# ===========================================================================

class TestH5ToRaster:
    def test_velocity_h5_creates_tif(self, tmp_path):
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        out = tmp_path / "velocity.tif"
        h5_to_raster(h5_path, out)
        assert out.exists()

    def test_timeseries_h5_creates_tif(self, tmp_path):
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(5, 10, 10).astype(np.float32)
        h5_path = tmp_path / "timeseries.h5"
        _make_mintpy_h5(h5_path, "timeseries", data)
        out = tmp_path / "timeseries.tif"
        h5_to_raster(h5_path, out)
        assert out.exists()

    def test_output_default_path_matches_stem(self, tmp_path):
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        h5_to_raster(h5_path)
        expected = tmp_path / "velocity.tif"
        assert expected.exists()

    def test_invalid_h5_name_raises(self, tmp_path):
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "unknownDataset.h5"
        _make_mintpy_h5(h5_path, "unknownDataset", data)
        with pytest.raises(ValueError, match="not recognised"):
            h5_to_raster(h5_path)

    def test_geo_prefixed_velocity_accepted(self, tmp_path):
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "geo_velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        out = tmp_path / "geo_velocity.tif"
        h5_to_raster(h5_path, out)
        assert out.exists()

    def test_output_is_geotiff(self, tmp_path):
        import rasterio
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        out = tmp_path / "velocity.tif"
        h5_to_raster(h5_path, out)
        with rasterio.open(str(out)) as src:
            assert src.driver == "GTiff"
            assert src.count >= 1

    def test_output_has_correct_nodata(self, tmp_path):
        import rasterio
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        out = tmp_path / "velocity.tif"
        h5_to_raster(h5_path, out)
        with rasterio.open(str(out)) as src:
            assert src.nodata == -9999.0

    def test_output_has_crs(self, tmp_path):
        import rasterio
        from insarhub.utils.postprocess import h5_to_raster
        data = np.random.rand(10, 10).astype(np.float32)
        h5_path = tmp_path / "velocity.h5"
        _make_mintpy_h5(h5_path, "velocity", data)
        out = tmp_path / "velocity.tif"
        h5_to_raster(h5_path, out)
        with rasterio.open(str(out)) as src:
            assert src.crs is not None


# ===========================================================================
# save_footprint
# ===========================================================================

class TestSaveFootprint:
    def test_creates_footprint_file(self, tmp_path):
        from insarhub.utils.postprocess import save_footprint
        data = np.ones((10, 10), dtype=np.float32)
        tif_path = tmp_path / "velocity.tif"
        _make_geotiff(tif_path, data)
        out = tmp_path / "footprint.geojson"
        save_footprint(tif_path, out)
        assert out.exists()

    def test_default_output_path(self, tmp_path):
        from insarhub.utils.postprocess import save_footprint
        data = np.ones((10, 10), dtype=np.float32)
        tif_path = tmp_path / "velocity.tif"
        _make_geotiff(tif_path, data)
        save_footprint(tif_path)
        # default output is .shp (shapefile)
        expected = tmp_path / "velocity_footprint.shp"
        assert expected.exists()

    def test_output_is_valid_geojson(self, tmp_path):
        import json
        from insarhub.utils.postprocess import save_footprint
        data = np.ones((10, 10), dtype=np.float32)
        tif_path = tmp_path / "velocity.tif"
        _make_geotiff(tif_path, data)
        out = tmp_path / "footprint.geojson"
        save_footprint(tif_path, out)
        content = json.loads(out.read_text())
        assert "type" in content
