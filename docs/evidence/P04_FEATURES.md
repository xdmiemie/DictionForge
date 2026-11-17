# P04 三段特征与真实加载器验收

日期：2026-11-17，Asia/Hong_Kong；RTX 5080 16 GB；独立 `.venv-worker`（依赖锁见 P02）。

## 实现

- `features.process(jobs, job)` 接收 `version_id`；冻结请求交给 `feature_worker`，成功和失败都保存特征报告，失败返回明确 `FAILED`。
- 特征目录为 `data/features/<version_id>`；只使用版本中 split=train 的片段。逐条验证发布音频 SHA256、上游资产 SHA256、唯一 basename、音素/BERT 维度、SSL [1,768,T] 与 32 kHz WAV 长度、语义 token 非空和范围，最后汇总索引。
- 实际依次执行锁定的 `1-get-text.py`、`2-get-hubert-wav32k.py`、`3-get-semantic.py`。本次特征精度明确为 fp32。上游单条失败即使退出码 0，应用仍会按预期集合判为失败。
- 缓存在 `data/feature-cache/{text,acoustic,semantic}/<fingerprint>`，包含逐文件 SHA256。文本、音频、语义分别失效；文本编辑不重建 SSL/语义。源码或资产锁变化会保守失效全部依赖。
- 缓存复制到每个版本独立的特征路径。缺失或损坏的目标文件可以由有效缓存恢复；缓存损坏则重新执行对应上游阶段。按真实验证后的条数提交三阶段进度。
- `normalize_config` 只接受已经接通字段；默认 S2→S1，物理 batch=1、fp16、停止方式 epochs、目标 1。短训验收明确选择 steps=2。配置和 fingerprint 作为预检快照保存。

## 真实预检边界

`build_loaders(area, config)` 实例化官方 S1 `Text2SemanticDataset`、S2 `TextAudioSpeakerLoaderV4`，使用官方 collate 和 bucket sampler。上游会把本次 5 条样本各膨胀成 100 条；适配层按唯一 basename 排序，显式消除 95 条隐式重复，不使用会静默缩小 batch 的 S1 DataModule。实际训练必须复用这个构造函数。

预检遍历所有 batch，校验张量数值并搬到 GPU；返回有效/过滤片段、实际批数、物理 batch、精度配置、原生扩容量、已去除重复量、sampler 补齐量和 GPU 资源。`num_workers=0` 避免预取游标歧义。S2 梯度检查点目前明确关闭，未开放未接通的高级选项。

0003-strict-v4-loader.patch 将 V4 音频/SSL 读取异常改为显式失败，移除上游错误后返回零张量的行为。补丁和修改前后内容 hash 都写入 `configs/upstream.lock.json`。

预检通过只证明真实加载器与输入张量可用；**本阶段的显存测量不是完整模型训练峰值**。返回 `gpu.probe=loader_tensors_only`、`training_memory=NOT_MEASURED`，P05 用真实前向/反向和优化器更新补足训练测量。

成功预检还绑定 `features_manifest_sha256`、`upstream_lock`、`asset_lock`、数据和配置 fingerprint。P05 必须重新核对这些绑定，不能拿旧预检开启改过输入的训练。

## 本次实测

输入发布版本：`76b5a08af4ca471f9c6fa9c903347e69`，真实知更鸟录音；6 条发布片段，其中 5 train / 1 holdout。发布原始清单和源录音未改动。

| 验收 | 结果 | 证据 |
| --- | --- | --- |
| A14 三段上游特征 | PASS；5 文本、5 BERT、5 SSL、5 WAV、5 语义行，当前锁下耗时 103.343 秒 | `P04_FEATURES_RESULT.json`，运行日志 `data/p04-features-final.log` |
| A15 缺失修复 | PASS；删除一个已生成 BERT 文件，3.703 秒从缓存补回，恢复文件 SHA256 一致；所有其他产物 hash 保持一致 | `P04_REPAIR_EVIDENCE.json`、`P04_REPAIR_RESULT.json` |
| A16 文本失效 | PASS；独立测试子版本 `10415066285f464f815b0a6ddcaaf22f` 只改一段文本：文本重建 1 / 复用 4，声学和语义各复用 5，耗时 60.296 秒 | `P04_TEXT_INVALIDATION_RESULT.json` |
| A17 真实 S1/S2 读取 | PASS；两阶段各 5 unique / 5 batches，batch=1；所有 batch 实际读取并搬到 RTX 5080，峰值张量分配 2,058,240 bytes | `P04_PREFLIGHT_RESULT.json`、`data/p04-preflight.log` |
| A17 零有效样本 | PASS（拒绝）；空特征夹具调用两个真实上游 Dataset 均返回 NO_TRAIN_BATCH，不得开始训练 | `P04_ZERO_BATCH_RESULT.json` |
| 配置/缓存回归 | 12 passed；非法参数、配置指纹、分层失效、缓存损坏、缺文件修复、发布数据不可变 | `tests/test_features.py` |

文本修改子版本只是缓存失效夹具，特意没有登记到业务版本列表，也未把修改后的文本冒充人工校对的训练标注。父版音频及清单保持不变。测试产物在 `data/p04-validation`；正式特征仍位于父版本的独立目录。

子进程退出后 `nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader` 显示 1229 MiB 已用 / 14749 MiB 空闲；本代理不再持有 GPU，随后主任务通过 HTTP 缓存准备和预检落业务记录。

## 可复现命令

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
.venv-worker/Scripts/python.exe -m dictionforge.feature_worker data/p04-features-request.json data/p04-features-result.json
.venv-worker/Scripts/python.exe -m dictionforge.feature_worker data/p04-preflight-request.json data/p04-preflight-result.json
.venv-worker/Scripts/python.exe -m dictionforge.feature_worker data/p04-text-request.json data/p04-text-result.json
.venv-api/Scripts/python.exe -m pytest tests/test_features.py -q --basetemp data/pytest-features-final
```

请求和完整运行日志保存在忽略的数据目录，Git 保存源码、补丁、配置与紧凑结果证据。缺资产、错误 hash、源码锁变化、未就绪特征、非法参数和零批次会给出可操作阻塞，未宣称通过本阶段之外的训练、恢复或推理验收。
