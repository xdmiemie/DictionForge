# P05–P06 训练适配设计

2026-11-17，基于固定上游 `6a1ece89928f6e5042a3ae0e092c2f47bdbae596` 的只读审计。本文件属于 P04 设计交付；下述训练、暂停、恢复保证尚待 P05/P06 实现和真实 GPU 验证，不代表通过验收。

## 1. 上游真实行为与必须处理的差异

| 位置 | 审计结论 | 应用适配要求 |
| --- | --- | --- |
| `AR/models/t2s_lightning_module.py::training_step` | 手动优化；每 batch backward；只有 `batch_idx > 0 and batch_idx % 4 == 0` 才 step/zero_grad/scheduler.step。第一次更新实际累积索引0–4共5批，之后4批；轮尾余梯度没有 flush，带入下一轮，Lightning ckpt不会自动保存 `.grad` | 不能把 batch、Lightning global_step或epoch相乘称作优化器更新；必须显式规定累积周期和轮尾处理，安全边界不得遗留未保存梯度 |
| `AR/modules/lr_schedulers.py::WarmupCosineLRSchedule` | `step()` 硬编码 `self.lr = self.end_lr = 0.002`，`set_lr()`也写end_lr；`configure_optimizers` 初始ScaledAdam lr为0.01。模板lr/warmup字段并未按名生效，`get_last_lr`还记录写入前值 | 不把模板值当实际学习率。采用可审计的配置映射，日志从 optimizer.param_groups读取；修复需补丁或明确独立适配实现，记录与原生差异 |
| `AR/data/data_module.py::train_dataloader` | DPO时batch减半，然后又按 `min(batch,len(dataset)//4)`缩小；num_workers=0却固定persistent_workers/prefetch | DPO首发关闭。预检应拒绝与生效值不一致配置，不偷偷缩小；单机可用明确DataLoader参数 |
| `AR/data/dataset.py::init_batch` | 过滤文本/语义缺项、最长语义、音素长度、音素每秒比；小于100条时按原列表重复扩展；0条会除零 | 报告unique和expanded样本及实际批数；零样本在实例化前/捕获异常时明确阻塞 |
| `module/data_utils.py::TextAudioSpeakerLoaderV4` | 从集合交集构建样本，重复扩展后`random.shuffle`；构造时还改变Python RNG；读取异常会打印后返回零张量 | 只set_epoch无法保证跨进程数据索引相同。冻结实际item映射；完整检查读取产物，训练时损坏不得当零样本继续 |
| `s2_train_v3_lora.py` | LoRA包裹`net_g.cfm`；目标为to_k/to_q/to_v/to_out.0，alpha=rank；AdamW收集所有requires_grad参数 | CFM底座被冻结，但**网络的ref_enc、bridge、wns1等仍训练**。不得错误冻结全模型或只导出lora_A/B |
| `module/models.py::SynthesizerTrnV3` | v4仍用该类；freeze_quantizer冻结ssl_proj/quantizer/enc_p；前向采样prompt_len，CFM用Python random和CUDA随机张量 | 恢复须保存Python、NumPy、Torch CPU和全部CUDA RNG；不能只设置初始seed |
| `s2_train_v3_lora.py::train_and_evaluate` | scaler可能跳过optimizer.step，但原global_step每batch递增；scheduler按epoch；初始化时先额外step若干次 | 独立记录processed_batches与真实optimizer_updates；调度器时机显式记录，不按epoch反推状态 |
| `utils.load_checkpoint` 与S2入口 | 宽泛except会回到底模；缺键/shape错用当前值替代；恢复点仅model/optimizer/iteration/lr | 应用恢复必须严格验证全部字段，失败就报CHECKPOINT_INVALID，禁止fallback |
| `s1_train.py` | Lightning完整ckpt和`weight/config/info`推理权重是两种文件；默认验证循环关闭 | 推理权重不能续训；不显示未实现的验证loss |

## 2. 明确采用的适配方向

独立 `training_worker.py` 复用固定上游的真实网络、前向损失、ScaledAdam/AdamW、数据集与collate，使用应用监督的单GPU训练循环。这样可在实际optimizer边界处理控制、保存和计数，不解析终端百分比猜进度。上游S1/S2模板仍生成并归档，模型与张量协议保持一致。任何改变原生错误累积/LR行为的选择需写入有效配置与验收记录；不能称作“原样运行官方脚本”。

S1建议首发`accumulation_steps=1`，每个完整物理batch对应一次更新，便于真实短训和精确恢复。若开放大于1，按连续cycle分组，轮尾不足完整组也flush；每组loss按实际组长平均，绝不让尾梯度跨epoch隐式流动。与上游五批首更的区别明确可见。首发使用真实配置的恒定学习率或经修复的warmup/cosine方案，不能直接暴露原生失效的lr参数。实际最终方案由P05实测确定并更新本节。

S2保留官方CFM LoRA及网络其余可训练分支，默认rank32。只从`requires_grad=True`收集优化参数；归档可训练键及元素总数。采用显式学习率与epoch后ExponentialLR时机，第一轮不执行未发生训练的预先衰减。开启GradScaler的FP16或无scaler的BF16/FP32必须在有效配置中准确显示，OOM不自动降batch/精度。

## 3. 与 P04 加载器的复用边界

`build_loaders` 应将同一冻结特征视图实例化为：

- S1：真实`Text2SemanticDataset`、collate、配置物理batch、明确num_workers，返回字典batch，保留`ids`映射到样本名。
- S2：真实`TextAudioSpeakerLoaderV4`、`TextAudioSpeakerCollateV4`、上游分桶边界`[32,300,400,500,600,700,800,900,1000]`，single replica/rank0，返回八元tuple。
- 同时提供dataset item键序列、实际expanded长度、unique键、loader批数、sampler seed/epoch、真实过滤报告。DataLoader首发num_workers=0，避免预取导致checkpoint“已发出”与“已消费”位置混淆。
- 训练直接使用同一构造器；不另写一套样本数预测器。每轮先物化实际sampler产生的batch indices，保存顺序和hash，再从cursor消费。S2的dataset映射跨进程稳定化，或恢复时按照检查点中的映射重排真实dataset。
- DataLoader自己的随机generator与模型RNG分离；构造dataset、sampler、iterator之后才恢复模型RNG。不能在恢复后通过遍历并丢弃旧batch“跳过”，否则随机状态/读取成本可能改变。

P04实现方已确认接口：`dictionforge.feature_worker.init_runtime(upstream)` 和 `build_loaders(area: Path, config) -> (loaders: dict, report: dict)`。`init_runtime` 设置固定上游cwd、模块路径、`version=v4`、`hz=25hz`。两段真实dataset按basename排序并去掉原生重复扩展，预检同时保留 `native_expanded_samples`、`removed_native_repeats`、`unique_samples`、`batches`、`batch_size`；应用轮次按去重后的实际loader定义，不能沿用原生100条重复的轮数含义。S1 sampler显式rank0/replica1/seed；S2用原生bucket batch sampler。P05直接复用该函数，不提前复制实现。

## 4. 真实更新、目标与事件

使用Torch Optimizer的step post-hook记录已成功返回的真实step。GradScaler因非有限梯度跳过时不会调用optimizer.step，不能增加更新数；另发overflow/skipped-update事件。不能依赖`scaler.step`返回值（许多优化器正常返回None），也不能只看scaler scale变化推算更新。

每次事件至少包含stage、epoch（已完成轮数与当前轮分别表示）、batch_cursor、processed_batches、optimizer_updates、skipped_updates、loss、实际lr、物理batch、精度、Torch峰值显存、elapsed、数据与配置fingerprint。速度和ETA仅在实际耗时/更新数存在后计算。

步数目标在达到真实更新之后立即保存/停止；若scaled step跳过，继续后续batch直至累计更新满足目标。轮次目标在该轮所有实际batch被消费且轮尾cycle已flush后达标。分母对应冻结目标单位，不把两个阶段步数直接相加成一种训练步。

## 5. 初始化与来源模型

### S1

新微调：构造与来源config匹配的Text2SemanticLightningModule/Decoder，严格加载`weight`，重新创建ScaledAdam与scheduler，计数和RNG按新run初始化。来源GPT权重的SHA256入快照。完整续训则加载完整checkpoint中的model/optimizer/scheduler等，不能用推理包“伪恢复”。

### S2 v4 LoRA

官方初始化：固定s2Gv4底模→构造v4模型→核对底模missing/unexpected键→创建一次CFM PEFT适配器→创建优化器。

来源模型初始化：同一锁定底模→创建一次相同rank/target modules适配器→加载来源包内全部可训练权重（LoRA及ref_enc/bridge/wns1等）→新优化器/新计数。此路径**不调用merge_and_unload**；禁止先把LoRA合并到底模再装同一LoRA。rank、底模hash或结构不兼容应拒绝。记录初始化后、第一步前的选定参数hash以及第一步后的hash，P11据此验证来源与官方两种初始化。

## 6. 完整恢复点与安全边界

恢复点在一次真实优化器更新完成、梯度清零后产生。发生累积时等待当前cycle完成；暂停请求到达后显示等待边界/保存中，只有checkpoint校验和last索引提交后才返回PAUSED。保存点包括：

| 内容 | 必须保存的状态 |
| --- | --- |
| 身份与兼容性 | schema_version、run/stage ID、上游commit/补丁hash、dataset与feature fingerprint、阶段config fingerprint、底模/来源权重hash、rank、trainable键 |
| 模型与优化 | 完整训练model.state_dict、optimizer.state_dict、scheduler.state_dict、适用GradScaler.state_dict；不只存推理LoRA权重 |
| 随机性 | Python random、NumPy RNG、Torch CPU RNG、所有CUDA RNG、DataLoader独立generator状态 |
| 数据消费 | 实际dataset item映射、当轮batch indices或可严格重建的签名、next_batch_cursor、epoch、completed_epochs、processed_batches、真实updates/skipped_updates |
| 边界签名 | grads_clear、accumulation_pending=0、scheduler_applied时机、next_epoch标志、原累计目标、阶段状态 |

新进程首先校验hash和兼容指纹，再构造同一网络与优化器，严格load state；实例构造与数据排序完成后最后恢复RNG。从保存的next_batch开始，不重复消费已确认batch。训练可以按相同cursor/更新数续训；逐位参数确定性只有连续运行与暂停重启对照实测后才声明。

写入流程：同卷临时文件→flush/fsync→重新torch.load/结构校验→SHA256→原子改名→原子更新checkpoint索引。失败保留旧last。周期点最多最近3个；暂停/交接/最终/被派生引用的点单独保护。写前按实际文件规模检查剩余空间，禁止为腾空间删除唯一有效点。恢复点不是资产下载目录中的模型，也不能清理原录音。

## 7. 推理快照格式与阶段交接

S1导出遵循`weight/config/info`；weight保留`model.`前缀，与TTS内Text2SemanticLightningModule严格加载对应。转为CPU FP16副本导出，不修改训练参数dtype。

S2导出遵循`process_ckpt.savee/my_save2`协议：`weight`包含所有训练中可更新参数，`config`为有效hps，`info`含真实epoch/updates，`lora_rank`为整数。v4 LoRA文件头为`b"04"`，其余保持Torch zip内容；不能把普通`torch.save`的`PK`文件改名后声称v4。结构检查用`load_sovits_new`和已知trainable键/rank/base依赖，GPU推理由TTS的底模→PEFT→加载→merge_once路径独立验收。

阶段完成必须先原子提交该阶段完整恢复点与不可变推理快照，再由父监督器推进stage index。未训练一侧配对其冻结初始权重，并标明来源。进程在交接两步间退出，重启根据已有stage完成收据补齐，不重新训练已完成stage。模型发布与GPU训练状态分开：导出失败只影响export记录，训练不回滚；同snapshot幂等重试。

建议worker通过追加且flush的JSONL输出真实事件；主进程按event序号去重写SQLite。控制请求带request_id和stage_id，以文件或持久队列传递；worker写控制receipt后主进程确认。具体CLI字段由主监督器与P05实现统一，尚未开放未实测功能按钮。

## 8. 真实验收安排

P05：使用P03景元版本，先S2 rank32/batch1短训和S1 batch1短训；对比初始化/完成权重，核对真实step hook、实际lr、累计步数和整轮目标，完成S2→S1以及单阶段配对。冻结实测精度、显存峰值、elapsed与产物SHA。

P06：S2和S1各执行一次真实pause，等待进程退出和显存释放，完整服务重启后恢复至原累计目标；保存并比较optimizer/scheduler/scaler/RNG/cursor。另做历史点派生、损坏/半写/磁盘满等受控I/O故障，以及交接窗口中断。没有实测的逐样本确定性或轮中恢复保证不得标记PASS。
