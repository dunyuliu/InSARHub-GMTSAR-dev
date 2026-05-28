# 程序结构

InSARHub 由三个松耦合的层次构成——**下载器（Downloader）**、**处理器（Processor）** 和 **分析器（Analyzer）**，每层均以命名后端的注册表形式实现。写入任务文件夹的 `insarhub_config.json` 会随流程推进逐步累积配置，因此每个阶段既可独立运行，也可串联执行。

- **下载器** — 在 ASF 搜索场景、基于质量评分选择干涉图配对，并获取 SLC 数据和轨道文件。
- **处理器** — 接收选定的配对，通过云端（HyP3）或本地 / HPC（ISCE2）生成地理编码干涉图。
- **分析器** — 将干涉图堆叠导入 MintPy，运行 SBAS 时序分析，输出速度场和形变图。

Web UI 和命令行均为同一 Python API 的轻量封装，在浏览器中运行的任何工作流均可在命令行或脚本中精确复现。

![InSARHub 工作流程](fig/InSARHub_workflow.png){: .doc-img-wide }
