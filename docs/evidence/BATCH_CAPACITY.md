# RTX 5080 物理 batch 容量实测

2026-11-18（Asia/Hong_Kong），NVIDIA RTX 5080 16GB / driver 621.01 / Torch 2.9.0+cu128。使用当前应用的 GPT-SoVITS v4 网络、loss、优化器、FP16 autocast/GradScaler 和原生 dataset/collate；SoVITS LoRA rank32，不启用梯度检查点，梯度累积1，official初始化、seed42。

每档在全新 worker 进程执行前向、反向和真实优化器更新，逐档增加后对失败区间二分。短测要求3次成功更新，上限及建议档分别复核12次；实际张量 batch 逐次断言，训练前后参数SHA必须改变。GPT部分档位出现初始GradScaler溢出跳过，单独记录且不计入成功更新。

| 数据工况 | 阶段 | 本轮通过上限 | 相邻OOM档 | 建议物理batch | 建议档CUDA分配峰值 | 建议档CUDA保留峰值 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 景元，最长 10 秒 | SoVITS v4 LoRA | 7 | 8 | **6** | 11.31 GiB | 11.54 GiB |
| 景元，最长 10 秒 | GPT | 49 | 50 | **32** | 9.56 GiB | 9.78 GiB |
| 流萤，最长 6.86 秒 | SoVITS v4 LoRA | 13 | 14 | **8** | 9.39 GiB | 9.61 GiB |
| 流萤，最长 6.86 秒 | GPT | 77 | 78 | **64** | 11.90 GiB | 12.16 GiB |

当前流萤数据建议 SoVITS=8、GPT=64；若包含接近10秒的片段，建议 SoVITS=6、GPT=32。应用参数上限仍为64，流萤GPT的77属于独立硬件压测结果，未更改界面限制或用户训练设置。梯度累积仍是单独参数；例如物理64×累积4的完整累积周期对应有效batch256，本次容量测试使用累积1。

结果是本轮固定条件下的实测边界。每档将CUDA分配器限制为当时独立空闲显存减256 MiB，以免Windows借用系统内存被误算为可用GPU容量；桌面占用会令临界值变化。建议档按已测缓存峰值保留至少15%的分配器预算余量。CUDA分配峰值不包含所有驱动/桌面开销，nvidia-smi整卡采样峰值另存于JSON，不能只用16 GiB除以单条占用推算。

景元数据有5条训练片段，使用其中最长10秒条目重复组成满批次；流萤有2471条训练片段，S2实际可用2471条、S1过滤后2452条，选择最长声学/语义及最长音素条目组成满批次（2条真实条目重复），覆盖较大补齐长度。重复仅用于容量压力测试，不代表新增训练样本，不发布模型。S2/GPT上限均通过12次真实更新，建议档也单独通过12次。不是完整训练的收敛或音质评价；更长片段、FP32或不同rank需要另测。

原有流萤特征任务b0f2876074a146e899427c8a67b159d1正常完成后，测试才取得应用的GPU锁。特征复制到独立目录；两轮结束均核对版本音频SHA及官方权重SHA不变，未发布模型、未改变默认训练参数。结束时无活动任务，应用健康，显存回落至897 MiB。

复现命令（先加载scripts/common.ps1并运行Set-DictionForgeEnvironment）：

```powershell
.venv-api/Scripts/python.exe scripts/benchmark_physical_batch.py --version 76b5a08af4ca471f9c6fa9c903347e69 --max-batch 64 --updates 3 --confirm-updates 12
.venv-api/Scripts/python.exe scripts/benchmark_physical_batch.py --version 4a87c1b08a0c411aae29c63e317f3aed --max-batch 128 --updates 3 --confirm-updates 12
```

完整本机报告、每档请求/生效配置/worker日志/GPU采样、参数SHA、更新/跳过计数位于：

- data/batch-benchmark/20261118-094659（景元10秒压力工况）
- data/batch-benchmark/20261118-095856（流萤6.86秒压力工况）

可版本控制的小型逐档结果为BATCH_CAPACITY.json；原始数据与特征副本留在data目录，不进入Git。只复验本轮压测脚本及真实GPU结果，未无关重跑API/UI全套。
