=== "默认"

    ??? note "创建新环境（推荐）"

        ```bash
        conda create -n insarhub python=3.12
        conda activate insarhub
        ```

    !!! note "Windows：请使用 Python 3.11"
        Windows 系统目前仅支持 Python 3.11 —— 创建环境时请使用 `python=3.11`。Linux 和 macOS 同时支持 3.11 和 3.12。

    从 conda-forge 安装最新版本：

    ```bash
    conda install insarhub -c conda-forge
    ```

    或通过 pip 安装（需先通过 conda 安装 GDAL）：

    ```bash
    conda install gdal
    pip install insarhub
    ```

=== "ISCE2 处理器"

    使用 ISCE2 `stackSentinel` 进行本地干涉图处理。

    !!! note "平台支持"
        ISCE2 仅支持 Linux 和 macOS (x86_64)，不支持 Windows 或 Apple Silicon，请使用 WSL2 或 Linux HPC 集群。

    先安装 InSARHub，再在同一环境中安装 ISCE2：

    ```bash
    conda install insarhub -c conda-forge
    conda install isce2 -c conda-forge
    ```

    通过pip安装：

    ```bash
    conda install gdal isce2
    pip install insarhub
    ```


    验证 ISCE2 安装：

    ```bash
    python -c "import isce; print(isce.__version__)"
    ```

---

### 开发环境配置

=== "默认"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    pip install -e .
    ```

    !!! note "Windows：请使用 Python 3.11"
        `environment.yml` 允许 Python 3.11 或 3.12，但 Windows 目前仅支持 3.11。如果依赖解析选择了 3.12，请在运行 `conda env create` 前将 `environment.yml` 中的 `python` 一行改为 `python=3.11`。

=== "ISCE2 处理器"

    ```bash
    git clone https://github.com/jldz9/InSARHub.git
    cd InSARHub
    conda env create -f environment.yml -n insar_dev
    conda activate insar_dev
    conda install -c conda-forge "numpy<2.0" isce2
    pip install -e .
    ```

??? note "使用 mamba 加速依赖解析"

    将以上命令中的 `conda` 替换为 `mamba`（如已安装 [mamba](https://mamba.readthedocs.io/en/latest/installation/mamba-installation.html)）。
