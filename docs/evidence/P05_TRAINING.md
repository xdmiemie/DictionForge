# P05 真实双阶段训练验收

2026-11-17（Asia/Hong_Kong），Windows 11 / RTX 5080 / Python 3.12.10 / Torch 2.9.0+cu128。使用 P03 景元数据版本 `76b5a08af4ca471f9c6fa9c903347e69`；真实三段特征来自 P04，5条唯一训练片段，1条真实留出片段不参与训练。

## 实现与上游差异

`training_worker.py` 是应用的独立单GPU训练循环，复用固定 GPT-SoVITS v4 的网络、真实损失、ScaledAdam/AdamW、数据集、collate和分桶采样器。**没有声称直接原封不动运行官方训练脚本**。P04 `build_loaders` 去除上游把小数据扩成100条的重复，实际每轮5个物理batch；epoch按该实际视图定义。

S1 使用上游 `forward_old` 与 ScaledAdam，恒定学习率0.002。累积4批，每周期loss按实际组长平均；5批的一轮为4+1，轮尾独立flush，消除上游首5批更新、尾梯度跨轮问题。实际学习率从optimizer读取，未使用上游硬编码/滞后报告的scheduler。S2保留官方CFM LoRA rank32/alpha32及ref_enc、bridge、wns1等所有可训练分支；AdamW学习率1e-4，ExponentialLR在完整epoch后衰减。两段batch1、FP16，无自动batch调整、无OOM降参。

进度由optimizer post-hook确认成功更新，GradScaler跳过不会增步。保存实际batch sizes、epoch/cursor、processed_batches、updates/skipped、loss、lr/next_lr、耗时/速度、CUDA峰值。阶段完成先提交完整checkpoint与推理snapshot，再原子提交stage_state，才交接下一段。S1归档有效JSON/YAML，S2归档有效JSON。旧验收运行只有JSON，主应用后续运行使用同时生成YAML的最终实现。

## 真实运行

命令：

```powershell
.venv-api/Scripts/python.exe scripts/validate_training_worker.py
.venv-api/Scripts/python.exe scripts/validate_training_worker.py --mode epochs --target 1
.venv-api/Scripts/python.exe scripts/validate_training_worker.py --stages s1 --mode steps --target 1
.venv-worker/Scripts/python.exe scripts/validate_training_worker.py --inspect-run data/p05-validation/runs/4d2e78633fc745258b24d30b5d9d209c
```

验证任务使用隔离的 `data/p05-validation/state`，不会被主应用监督器竞争领取。训练特征仅复制，不改写原数据。所有实际请求、预检、日志、阶段状态、检查点、推理快照、结果位于对应 `data/p05-validation/runs/<id>`。

| 运行 | 结果与真实计数 |
| --- | --- |
| `8d75c93b254d4f9bb222ce77ef9e1a02` | 首次S2真实2次更新成功，16.593秒、峰值3,818,707,456 B，完整点和75,550,395 B v4快照均保存。交接S1时accuracy返回float，适配层错误地调用detach导致失败；已修复，S2成果未丢失。 |
| `6d84579aedaa40bbaf5f43c19e22415c` | 双阶段steps2完成。S2更新2/skip0、3.578秒。S1使用原始GradScaler初值65536，实际skip9后更新2；共消费29batch、完成5轮、最终scale128。skip没有计入目标，这是实际跳步证据。 |
| `4d2e78633fc745258b24d30b5d9d209c` | 调整为显式记录的S1 scaler初值128后，双阶段epochs1完成：S2消费5batch并更新5；S1消费5batch，按4+1产生2次更新，skip0，尾周期group_size1。两段模型可训练参数SHA均发生变化。 |
| `33628c0839bb4714b7a451d3ec4c1d1a` | 单阶段S1 steps1完成：cursor4/5、消费4batch、更新1、skip0、completed_epochs0；S2标SKIPPED，配对冻结官方SoVITS权重。证实轮内准确停止。 |

最终S1 scaler初值128写入effective配置；S2保留65536。这是本机实测确定的明确默认值，不是训练中偷偷变更batch/精度。后续动态scaler的变化仍按真实值报告。原始65536的跳步记录保留。

上述早期验收及主HTTP首次验收的effective文件仍含上游模板 `train.epochs` 和warmup等旧字段；实际停止由冻结请求的 `stop_mode/target` 控制，S1恒定LR由实际优化器记录证明。最终实现已把epochs/max_steps写为实际目标或null，将未启用的模板warmup、梯度裁剪及日志字段移至 `ignored_template_fields`，明确S1内部ScaledAdam裁剪及S2整轮ExponentialLR策略，并把实际num_workers写为0。此修正只澄清归档配置，没有改动训练计算，未重写既有真实证据。

## 完整检查点与推理快照

整轮与单阶段运行均用独立CPU进程重新读入全部检查点并通过：

- model、optimizer、scheduler、scaler完整state；Python/NumPy/Torch CPU/全部CUDA RNG；真实dataset键顺序、当轮batch indices、cursor；源码/补丁锁、资产锁、配置、数据、特征manifest指纹、初始化权重SHA。
- 安全边界 `grads_clear=true`、`accumulation_pending=0`。按trainable键从检查点重算SHA，与真实训练结束的参数SHA完全相等。
- S1 `.ckpt` 的weight/config/info结构；S2 `.pth` 的v4 `04`头与lora_rank。逐个导出tensor等于完整checkpoint对应tensor转FP16后的值，包含LoRA外全部应导出分支。
- 文件采用同卷临时写入、flush/fsync、重新加载、SHA256校验、原子改名，之后才更新checkpoint index/last。推理快照与完整恢复点分开。

真实GPU加载导出权重并合成的模型发布验收在 P07/P08 独立记录；本阶段的结构/数值一致检查不代替该验收。完整暂停/重启续训在 P06 实现并验证，本阶段没有开放未验收的暂停按钮。

## 测试与证据

- `tests/test_training_worker.py`：批序物化、尾周期、从cursor直接取余批、实际配置/无效模板字段区分；3项通过。
- 独立review追加的真实CPU GradScaler合同测试验证有限→Inf→有限三次尝试只有两次optimizer hook，Inf跳步不改变参数，并能保存/恢复scaler。它是控制合同测试，不冒充GPU训练。
- 实际结果归档 `P05_TRAINING.json`；checkpoint-inspection结果同时位于对应运行目录。
- 模型/优化器大文件保留在忽略的data目录，Git只提交实现、锁和小型验收证据。
