# P06 检查点完整性与磁盘故障验收

日期：2026-11-17。本报告覆盖 CPU 文件完整性、引用保护和真实 Torch 序列化故障；真实 GPU 暂停、恢复与派生见 P06 主验收报告。测试中小型字节夹具不作为可推理权重。

## 执行与结果

```powershell
.\.venv-api\Scripts\python.exe -m pytest tests/test_checkpoints.py tests/test_checkpoint_torch_io.py -q --basetemp E:\DictionForge\data\pytest-checkpoints-verified
```

结果 **20 passed in 8.73s**。原始测试夹具在 `data/pytest-checkpoints-verified`，机器可读摘要在 [P06_CHECKPOINT_IO.json](P06_CHECKPOINT_IO.json)。Torch 子进程使用 `.venv-worker` 的 `torch 2.9.0+cu128`，显式设置 `CUDA_VISIBLE_DEVICES=-1`。

Windows junction 用原生 PowerShell 在上述专用夹具目录创建，检查点路径校验明确拒绝，外部目标文件仍完整；最后只删除该 junction。默认沙盒拒绝创建 junction，重新以已授权本机权限运行上述全部测试后 20 项均通过。

## 已验证行为

| 场景 | 结果 |
| --- | --- |
| `.part` 半写与已写完但未登记的孤儿 | 不出现在可选列表，不自动恢复 |
| 最新 checkpoint 同尺寸坏哈希 | 显示失败点与最近有效回退点；显式选择损坏 ID 必须失败 |
| 索引 last 指向不存在 ID | 明确返回 `INDEX_LAST_MISSING` 和实际回退点 |
| 快照缺失、尺寸不符、哈希不符、临时文件 | 完整恢复点校验失败 |
| 绝对路径、`..` 相对路径、Windows junction 越界 | 拒绝读取和恢复 |
| 旧 P05 阶段最终点无 snapshot 索引 | 列表给兼容警告；暂停/周期点缺快照则失败，完整状态由 worker 再验 |
| 重复 ID、损坏索引、损坏引用登记 | 明确失败，不能静默底模初始化或据此删除 |
| 真 Torch 写入前磁盘空间估算不足 | 提前拒绝，无目标文件或 `.part` 残留 |
| 真 Torch 临时文件写一部分后注入 ENOSPC | 清理半写文件，已有完整文件不受影响 |
| 不可变 Torch 文件被重复写入 | `IMMUTABLE_ARTIFACT_EXISTS`，原字节保持一致 |
| 真 Torch 完整写完、索引原子替换时注入 ENOSPC | 原索引/last 保留；新完整文件作为未登记孤儿保留 |
| 保留最近 3 个周期点 | 更老无引用周期点从索引移除，再删除对应 checkpoint 与 snapshot |
| 暂停、阶段最终、last、最近有效、损坏诊断、派生/当前恢复引用 | 始终保护 |
| retention 索引发布失败 | 所有文件、旧索引与 last 均保留 |
| retention 单文件删除失败 | 已移除索引条目；孤儿路径和原因明确返回，其余有效恢复点不受影响 |
| retention 与派生登记并发 | 持 SQLite `BEGIN IMMEDIATE`，独立连接申请写锁被拒绝；派生与继续训练在登记事务内再次选择恢复点 |

`apply_retention` 不递归删除目录，仅对已验证位于该运行 `checkpoints/` 和 `snapshots/` 的单个文件执行 `Path.unlink()`。保存点只在快照与全状态都写完、回读、计算哈希之后发布 index。索引落盘失败时已有 last 不变；清理失败时仍可恢复最新完整点。

## 独立恢复审查

核对了模型、优化器、scheduler、scaler、Python/NumPy/Torch CPU/CUDA RNG 恢复顺序；固定数据项顺序、完整 epoch 批序和 cursor；S1 累积尾组；S2 epoch 结束后的 scheduler 状态；历史点只继承其前序已完成阶段。上游 S2 sampler 使用独立 Generator，ScaledAdam 状态仍由标准 optimizer state_dict 保存。已修复的真实竞态为：派生/继续先在事务外选择恢复点，可能被周期清理抢先移除；当前已在同一数据库写事务内重查。

本报告不把文件哈希通过视为全训练状态兼容；worker 在反序列化后仍检查运行/阶段、配置、版本、特征内容、源码及资产锁、初始权重、数据项、参数键和安全梯度边界。
