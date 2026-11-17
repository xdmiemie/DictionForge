# P06 完整恢复点复读与数值对照

2026-11-17。真实训练使用 RTX 5080、Torch 2.9.0+cu128、固定 GPT-SoVITS v4 上游及已锁资产；数据版本 `76b5a08af4ca471f9c6fa9c903347e69`，5 条训练样本。S2/S1 均以 20 次真实优化器更新为累计目标，物理 batch=1、FP16、seed=42；S2 rank32、LR=0.0001，S1 accumulate4、恒定 LR=0.002。

父运行 `be4045700e60491b95efb7101aaf411a` 经两段真实暂停、整服务重启、交接窗口和 S1 轮内进程中断后完成。子运行 `a1e76ef07ae34bedb9dbb45590216c2f` 从父 S2 u3 历史点派生，在自己的 u5 点暂停并继续，最终两段也各完成 20 次更新。控制、进程退出、服务重启和父文件保护证据见 `P06_HTTP.json`；此报告独立复读全部 7 个已登记点。

## CPU 完整复读结果

执行 `.venv-worker\Scripts\python.exe scripts\inspect_p06.py`，未在检查进程构造 CUDA 模型。父子两运行全部检查通过，结果为 `P06_TRAINING_INSPECTION.json`。

| 运行与点 | optimizer state 中 step | epoch / cursor / 消费 batch | scheduler last_epoch | scaler growth_tracker |
| --- | ---: | --- | ---: | ---: |
| 父 S2 pause u3 | 3 | 0 / 3 / 3 | 0 | 3 |
| 父 S2 final u20 | 20 | 3 / 5 / 20 | 4 | 20 |
| 父 S1 pause u3 | 3 | 1 / 4 / 9 | 3 | 3 |
| 父 S1 final u20 | 20 | 9 / 5 / 50 | 20 | 20 |
| 子 S2 pause u5 | 5 | 0 / 5 / 5 | 1 | 5 |
| 子 S2 final u20 | 20 | 3 / 5 / 20 | 4 | 20 |
| 子 S1 final u20 | 20 | 9 / 5 / 50 | 20 | 20 |

epoch 为从 0 开始的当前轮编号，cursor 为该轮已消费 batch 数。S1 每轮 5 batch 按 4+1 两个周期更新，没有丢弃尾周期。S2 scaler scale=65536，S1 scale=128；本次两段均无 scaler 跳步。

每个点都重新读取 model、完整 optimizer/scheduler/scaler、Python/NumPy/Torch CPU/全部 CUDA RNG、冻结 batch_order 和游标、配置/数据/特征/上游/资产指纹及初始化权重哈希。检查确认 `grads_clear=true`、`accumulation_pending=0`，重新计算的可训练参数 SHA256 与保存时相等。每个不可变推理快照的全部 tensor 均等于对应完整点 tensor 转 FP16 的值，S2 v4 `04` 文件头和 rank32 正确。每个 RNG 分量的哈希保留在 JSON 中。

父 S1 恢复时读入 u3 的参数 SHA256 `0828f624758a51cdbfd089201ddc915bca2e96c7cd101b607431f60735c1f5e0`，从 epoch1/cursor4 直接读取剩余批次。S2 已完成 u20 后的交接恢复复用该点，计数仍为 20，没有重新训练这一阶段。子运行再次续训明确使用自己的 `resume_source_run`，永久谱系 `parent_run` 不会把它错误指向父恢复点。

## 父子最终参数的实际差异

这两条真实轨迹的最终四组 RNG 哈希分别相同，最终参数没有达到位级一致。对全部可训练 FP32 参数的 CPU 数值比较如下；不据此宣称任意配置的位级确定性，也不把差异推测为已证明的单一原因。

| 阶段 | 参数数 | 最大绝对差 | 全参数 RMS 差 | 位级相同 |
| --- | ---: | ---: | ---: | --- |
| S2 | 36,155,748 | 0.0002374362520640716 | 0.0000009378031927421545 | 否 |
| S1 | 77,606,402 | 0.003993988037109375 | 0.000004264428446933878 | 否 |

## 其他恢复合同与修复

- 真实测试发现 Windows 短时读句柄阻止 `stage_state.json` 原子替换；公共原子 JSON 写入增加有限重试，旧确认恢复点保留，随后从同一点成功恢复。原错误留在 HTTP 验收记录。
- 上游 S1 sampler 会推进 Python 全局 RNG。轮末恢复跨到下一轮时，现保留 sampler 推进后的 RNG，再在 DataLoader iterator 创建后恢复。实际 `train_stage`、锁定上游 sampler 和 CPU 小模型的随机样本合同测试通过；它明确属于 CPU 合同测试。
- 实际 `train_stage` 的 CPU 101 更新测试确认 u100 为 periodic、u101 为 stage_final。真实 GPU 本次目标 20，未把 CPU 周期测试声称为 GPU 百步长训。
- 每 100 个真实 optimizer updates 写周期完整点及不可变快照。索引提交后执行保留最近 3 个周期点，暂停/阶段终点/last/派生或待恢复引用受保护。清理持数据库写锁重新检查引用，先原子提交索引再删除；失败保留孤儿并报告，不破坏有效点。
- 真实 Torch CPU 序列化故障测试覆盖预估磁盘不足、半写失败、index 替换失败；索引/保留测试覆盖引用保护及路径别名。相关批次 27 PASS；默认沙盒中的 Windows junction 项跳过后，已在独立允许环境中真实创建并通过。实际循环/RNG/周期另 2 PASS。

本报告验证恢复与保存状态，不替代后续真实推理加载、音质试听或模型发布验收。
