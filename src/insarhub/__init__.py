#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import platform
import sys
import logging

from pathlib import Path
from colorama import init
init(autoreset=True)
from colorama import Fore, Style, Back

logging.disable(logging.CRITICAL)
from insarhub._version import __version__
_system_info = platform.system()

# ---------------------MintPy Configuration-----------------------------------
# MintPy is a required dependency.
# Configuration followed the MintPy post-installation setup
# https://github.com/insarlab/MintPy/blob/main/docs/installation.md#3-post-installation-setup
import mintpy

# b. Dask for parallel processing (MintPy's compute_cluster option)
from dask import config as dask_config
tmp_dir = Path.home().joinpath('.dask','tmp')
tmp_dir.mkdir(parents=True, exist_ok=True)
dask_config.set({'temporary_directory':str(tmp_dir)})

# c. Extra environment variables setup
os.environ["VRT_SHARED_SOURCE"] = "0"
os.environ["HDF5_DISABLE_VERSION_CHECK"] = "2"
os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

# ---------------------pyproj PROJ data-dir fix (conda envs)-----------------
# In some conda environments, pyproj's compiled extension dynamically links
# against the environment's own shared libproj.so rather than pyproj's
# private bundled copy, but pyproj's default data-dir resolution still points
# at that private bundled copy — a mismatch that makes every CRS/Transformer
# call fail with "proj_create: no database context specified", even though
# the environment's own PROJ install is otherwise completely fine and the
# `PROJ_DATA`/`PROJ_LIB` env vars are set correctly (pyproj doesn't honor
# either at the point its internal context is first created). Redirect
# pyproj to the running interpreter's own PROJ data directory when one is
# present. sys.prefix (the actual running interpreter's install location) is
# used rather than $CONDA_PREFIX, which only reflects the shell's *activated*
# environment and can point at the wrong one if a different env's Python is
# invoked directly (e.g. by absolute path) without activating it first.
for _proj_prefix in (sys.prefix, os.environ.get("CONDA_PREFIX")):
    if not _proj_prefix:
        continue
    _candidate_proj_data = Path(_proj_prefix) / "share" / "proj"
    if (_candidate_proj_data / "proj.db").is_file():
        import pyproj
        pyproj.datadir.set_data_dir(str(_candidate_proj_data))
        break

# ---------------------Check runing environment -----------
if 'SLURM_MEM_PER_NODE' in os.environ:
    if int(os.environ['SLURM_MEM_PER_NODE'])<=512:
        # Value is small, assuming GB
        _memory_gb = int(os.environ['SLURM_MEM_PER_NODE'])
    elif int(os.environ['SLURM_MEM_PER_NODE'])>512&int(os.environ['SLURM_MEM_PER_NODE'])<=524288:
        # This range of value would assume to be MB
        _memory_gb = int(int(os.environ['SLURM_MEM_PER_NODE'])/1024)
    elif int(os.environ['SLURM_MEM_PER_NODE'])>524288:
        # Value is too large, assume mem is KB
    
        _memory_gb = int(int(os.environ['SLURM_MEM_PER_NODE'])/(1024**2))
    _cpu_core = int(os.environ['SLURM_CPUS_PER_TASK'])
    _manager = 'slurm'
elif 'PBS_NUM_PPN' in os.environ:
    _memory_gb = int(int(os.environ['PBS_MEM']))
    _cpu_core = int(os.environ['PBS_NUM_PPN'])
    _manager = 'pbs'
elif 'LSB_JOB_NUMPROC' in os.environ:
    _memory_gb = int(int(os.environ['LSB_JOB_MEMLIMIT'])/1024)
    _cpu_core = int(os.environ['LSB_JOB_NUMPROC '])
    _manager = 'lsf'
else:
    import psutil
    _memory_gb = round(psutil.virtual_memory().total/1024**3)
    _cpu_core = os.cpu_count()
    _manager = 'local'

_env = {
        "memory": _memory_gb,
        "cpu": _cpu_core,
        "manager": _manager,
        "system": _system_info,

    }
# ---------------------package imports---------------------

from .core.registry import (
    Downloader,
    Processor,
    Analyzer,
)

from .core.base import (
    BaseDownloader,
    LocalProcessor,
    CloudProcessor,
    BaseAnalyzer,
)

from .config.defaultconfig import (
    ASF_Base_Config,
    Hyp3_Base_Config,
    Mintpy_SBAS_Base_Config,
    S1_SLC_Config
)   

from .downloader import (
    ASF_Base_Downloader,
    S1_SLC,
)

from .processor import (
    Hyp3_S1,
    ISCE_S1,
)

from .analyzer import (
    Mintpy_SBAS_Base_Analyzer,
    Hyp3_SBAS,
    Hyp3_SBAS_Config,
    ISCE_SBAS,
)

from .downloader.s1_slc import S1_SLC_Config

from .utils import (
    tool,
    postprocess,
    batch
)

__all__ = [
    "BaseDownloader",
    "LocalProcessor",
    "CloudProcessor",
    "BaseAnalyzer",
    "Downloader",
    "Processor",
    "Analyzer",
    "ASF_Base_Config",
    "ASF_Base_Downloader",
    "S1_SLC",
    "S1_SLC_Config",
    "Hyp3_S1", 
    "Mintpy_SBAS_Base_Config",
    "Mintpy_SBAS_Base_Analyzer",
    "Hyp3_SBAS",
    "Hyp3_SBAS_Config",

]



