The InSARHub analyzer module provides workflow for InSAR time-series analysis.

- **Import analyzer**

    Import the Analyzer class to access all time-series analysis functionality
```python
from insarhub import Analyzer
```

- **View Available Analyzers**

    List all registered analyzers
```python
Analyzer.available()
```

## Available Analyzers

=== "Mintpy_SBAS_Base_Analyzer"

    InSARHub wrapped [Mintpy](https://github.com/insarlab/MintPy) as one of its analysis backends. The `Mintpy_SBAS_Base_Analyzer` is implemented on top of a reusable base configuration class, which provides the full `smallbaselineApp` logic of Mintpy. Provides users with an experience similar to using MintPy directly, allowing full customization of processing parameters and steps.

    ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer with Parameters**

        Initialize an analyzer instance

        ```python
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer',
                                    workdir="/your/work/dir",
                                    load_processor="hyp3", ....)
        ```
        OR
        ```python
        params = {"workdir": "/your/work/dir", "load_processor": "hyp3" ....}
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', **params)
        ```
        OR
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir",
                                      load_processor="hyp3",
                                      ....)
        analyzer = Analyzer.create('Mintpy_SBAS_Base_Analyzer', config=cfg)
        ```

        The base config `Mintpy_SBAS_Base_Config` contains all parameters from Mintpy `smallbaselineApp.cfg`. For detailed descriptions refer to the [official Mintpy config documentation](https://github.com/insarlab/MintPy/blob/054c6010b5e40e98fe16e283121fdd1ae4bc1732/src/mintpy/defaults/smallbaselineApp.cfg).

        ::: insarhub.config.Mintpy_SBAS_Base_Config
            options:
                members: false
                heading_level: 0

    - **Run**

        Run the Mintpy time-series analysis based on provided configuration

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.run
            options:
                members: true
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Generate a single `sbatch` script covering all selected steps and submit it to SLURM. Inherited by `Hyp3_SBAS` and `ISCE_SBAS`.

        ```python
        # Submit full pipeline as one SLURM job
        analyzer.submit_hpc()

        # Submit only specific steps
        analyzer.submit_hpc(steps=["velocity", "geocode"])
        ```

        The script is written to `<workdir>/mintpy/mintpy_sbas.sbatch` and job state to `mintpy/mintpy_job.json`. SLURM resources come from `<workdir>/sbatch_options.json`, step key `"17"` — the same file `ISCE_S1`'s own HPC submission uses for steps `01`–`16`, since the processor and analyzer typically share one workdir. Default: `time=24:00:00`, `ntasks=1`, `cpus_per_task=16`, `mem=128G`, `partition=all`.

        `submit_hpc()` returns the SLURM job ID string on success, or `None` if `sbatch_options.json` was just created (or updated with a missing `"17"` entry) — callers should check for `None` and stop rather than treat it as a successful submission:

        ```python
        cfg = Mintpy_SBAS_Base_Config(
            workdir="/your/work/dir",
            load_processor="hyp3",
            hpc_mode=True,
        )
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        job_id = analyzer.submit_hpc()
        if job_id is None:
            print("sbatch_options.json was just created/updated — review it, then resubmit.")
        ```

        Edit step `"17"` in `sbatch_options.json` directly to change resources (e.g. `{"17": {"time": "48:00:00", "mem": "256G", "partition": "gpu"}}`), then call `submit_hpc()` again.

        ::: insarhub.analyzer.mintpy_base.Mintpy_SBAS_Base_Analyzer.submit_hpc
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Clean up**

        Remove intermediate processing files generated during the time-series process

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "Hyp3_SBAS"

    The `Hyp3_SBAS` is a specialized analyzer that extends `Mintpy_SBAS_Base_Analyzer`, preconfigured specifically for processing time-series data from HyP3 InSAR products.

    ::: insarhub.analyzer.Hyp3_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer with Parameters**

        Initialize an analyzer instance

        ```python
        analyzer = Analyzer.create('Hyp3_SBAS',
                                    workdir="/your/work/dir")
        ```
        OR
        ```python
        params = {"workdir": "/your/work/dir"}
        analyzer = Analyzer.create('Hyp3_SBAS', **params)
        ```
        OR
        ```python
        from insarhub.config import Mintpy_SBAS_Base_Config
        cfg = Mintpy_SBAS_Base_Config(workdir="/your/work/dir")
        analyzer = Analyzer.create('Hyp3_SBAS', config=cfg)
        ```

    - **Prepare data**

        Prepare interferogram data downloaded from HyP3 server for MintPy

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.prep_data
            options:
                members: false
                heading_level: 5

    - **Run**

        Run the Mintpy time-series analysis based on provided configuration

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.Hyp3_SBAS.run
            options:
                members: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Inherited from `Mintpy_SBAS_Base_Analyzer`. Submit full MintPy run as a single sbatch job.

        ```python
        analyzer.submit_hpc()
        ```

    - **Clean up**

        Remove intermediate processing files generated during the time-series process

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.Mintpy_SBAS_Base_Analyzer.cleanup
            options:
                members: true
                show_source: false
                heading_level: 5

=== "ISCE_SBAS"

    The `ISCE_SBAS` analyzer extends `Mintpy_SBAS_Base_Analyzer` and is preconfigured for ISCE2 `stackSentinel` outputs. `prep_data()` auto-discovers interferograms and geometry from the `isce/` directory and writes the MintPy config to `mintpy/.mintpy.cfg`. All MintPy outputs are written to `workdir/mintpy/`.

    ::: insarhub.analyzer.isce_sbas.ISCE_SBAS
        options:
            members: false
            heading_level: 0

    ### Usage

    - **Create Analyzer**

        ```python
        from insarhub import Analyzer

        analyzer = Analyzer.create('ISCE_SBAS', workdir='/your/work/dir')
        ```

        OR with explicit config:

        ```python
        from insarhub.config.defaultconfig import ISCE_SBAS_Config

        cfg = ISCE_SBAS_Config(workdir='/your/work/dir')
        analyzer = Analyzer.create('ISCE_SBAS', config=cfg)
        ```

        ::: insarhub.config.defaultconfig.ISCE_SBAS_Config
            options:
                members: false
                show_source: false
                heading_level: 0

    - **Prepare data**

        Auto-discover ISCE2 outputs and write `mintpy/.mintpy.cfg`.

        ```python
        analyzer.prep_data()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.prep_data
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Run**

        Run MintPy SBAS time-series analysis. All output written to `workdir/mintpy/`.

        ```python
        analyzer.run()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.run
            options:
                members: false
                show_source: false
                heading_level: 5

    - **Submit (HPC / SLURM mode)**

        Inherited from `Mintpy_SBAS_Base_Analyzer`. Submit full MintPy run as a single sbatch job.

        ```python
        analyzer.submit_hpc()
        ```

    - **Clean up**

        Remove large ISCE2 intermediate directories and input data no longer needed after `load_data`.
        Removes `isce/coarse_interferograms/`, `isce/ESD/`, `isce/coreg_secondarys/`, `isce/interferograms/`, `slc/`, and `dem/`.

        ```python
        analyzer.cleanup()
        ```

        ::: insarhub.analyzer.isce_sbas.ISCE_SBAS.cleanup
            options:
                members: false
                show_source: false
                heading_level: 5
