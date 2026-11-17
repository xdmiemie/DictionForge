# P00 环境勘察

2026-11-17，Asia/Hong_Kong。本阶段只读检查；未安装、未修改 TimbreForge 或原始录音，尚未运行 GPU 探针。

## 本机运行环境

- 可用 Python 3.12.10：`E:\astrbot-chat\external\Python312\python.exe`。
- `E:\TimbreForge\.venv-rift` 为 Python 3.12.10，元数据记录 Torch `2.9.0+cu128`、Torchaudio `2.9.0+cu128`、Lightning `2.5.5`、NumPy `2.3.4`、Transformers `4.57.1`、Librosa `0.11.0`。
- RIFT wheelhouse：128 个文件，3,145,269,651 字节；可只读复用下载缓存，安装至 DictionForge 自己的环境。API wheelhouse 有 Python 3.12 Windows wheels。
- GPT-SoVITS 固定基线要求 NumPy `<2.0`、Librosa `0.10.2`、FunASR `1.0.27`、Torchmetrics `<=1.5`；不能把 RIFT 环境直接认定为兼容环境。
- 本次检查 E: 剩余约 28.0 GB，D: 剩余约 140.2 GB。独立 worker、模型、检查点并存时须检查空间并保留最后有效检查点。
- NVIDIA RTX 5080、驱动 621.01、16303 MiB 显存已由主任务 nvidia-smi 确认。Torch 实际 CUDA 运算、峰值显存和退出释放需要 P02 实测。

## 可用真实录音及标注

由 TimbreForge 当前数据版本清单定位，并只读确认原目录存在：

| 目录 | WAV 数量 | WAV 字节总计 |
| --- | ---: | ---: |
| `D:\中文 - Chinese\知更鸟` | 662 | 408,428,968 |
| `D:\中文 - Chinese\景元` | 651 | 439,313,206 |

两个目录包含同名 `.lab` 文本。例如 `archive_robin_1.lab` 对应知更鸟自我介绍，`archive_jingyuan_1.lab` 对应景元自我介绍。两份文本为非空中文且能 UTF-8 读取；是否逐字匹配仍须试听确认。后续只读原文件，在 DictionForge 的数据目录制作副本和切片，不覆盖、移动或清理来源。

TimbreForge 另有 `data/fixtures/角色 验收/en-00.wav` 至 `en-19.wav` 共 20 个短音频。旧验收报告说明它们是本机合成语音；本次未找到对应生成文本，不以其作为准确标注夹具。优先使用上述真实录音与 `.lab`。

## v4 下载基线

只读查询 `https://huggingface.co/api/models/lj1995/GPT-SoVITS/tree/main?recursive=true&expand=false&limit=1000` 得到当前文件大小，尚不作为资产锁（P02 须固定 revision 和哈希）：

| 资产 | 字节 |
| --- | ---: |
| `s1v3.ckpt` | 155,284,856 |
| `gsv-v4-pretrained/s2Gv4.pth` | 769,025,545 |
| `gsv-v4-pretrained/vocoder.pth` | 57,781,109 |
| `chinese-hubert-base/pytorch_model.bin` | 188,811,417 |
| `chinese-roberta-wwm-ext-large/pytorch_model.bin` | 651,225,145 |

五个主要权重约 1.82 GB，另需配置、分词器、中文前端、FunASR 识别/标点/VAD 资产。官方 v4 标签提供 50 系显卡 Windows 整合包，但本次尚未下载。

## P02 待验证

独立安装可用组合；CUDA 张量探针；中文路径 FFmpeg 解码；真实 FunASR；官方 v4 48 kHz 合成；依赖全量锁、资产 SHA256、实际配置及耗时。以上结果均不得从 TimbreForge 已通过的 RIFT 测试推定。
