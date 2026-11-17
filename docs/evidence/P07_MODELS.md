# P07 模型包与引用保护验收

日期：2026-11-17。模型包采用不可变快照、独立 CPU 校验、同卷原子发布和最后登记。`READY` 只表示完整结构校验通过；GPU 试听单独记录。三种训练导出和真实 48 kHz GPU 试听由 P07 主验收记录覆盖，本文件记录 CPU 与管理合同。

## 交付接口

- `exports.enqueue_snapshot` 冻结权重对、配置、数据版本、阶段进度、导出原因、源码及资产锁；`model_export` 任务独立发布。每个阶段标注 `weight_source` 与 `weight_sha256`，未开始阶段明确保留初始化权重。
- `exports.retry` 接受导出 job ID 或 snapshot ID；每次重试仍使用原 snapshot ID 和 model version ID。`retry_intent` 处理尚未成功入队的持久化导出意图。
- `models.list_models` 平铺已注册版本并包含官方模型；`resolve` 重验实际权重、依赖与默认参考。外部导入先复制到独立快照，再经过同一 CPU 校验与发布流程，原文件不修改。
- `references.delete_preview/delete` 列出确切路径、字节数、哈希和引用；确认 token 有效期 10 分钟，提交时在数据库写事务内重新核对。文件先移至受控回收区，元数据提交后清理；启动对账恢复未提交的移动或继续待清理文件。

## 真实 CPU 权重校验

```powershell
$env:PYTHONPATH='E:\DictionForge\backend'
$env:CUDA_VISIBLE_DEVICES='-1'
.\.venv-worker\Scripts\python.exe -m dictionforge.model_worker data/diagnostics/p07-model-request.json data/diagnostics/p07-model-result-final.json
.\.venv-worker\Scripts\python.exe scripts/validate_model_packages.py data/diagnostics/p07-model-request.json data/diagnostics/p07-model-negative-final
```

有效权重对来自真实运行 `7a00f578a57f4c8895e481e5ba67413e`。Torch `2.9.0+cu128`、CPU、CUDA 未初始化。GPT 295 个张量，词表 1025、音素表 732、24 层；SoVITS 为 `04` v4 LoRA、rank 32、258 个张量。全部键名、尺寸、数值有限性及适用架构通过。

SoVITS 结构由锁定上游在 meta device 构造，涵盖所有可训练分支和 buffer，避免另分配整套模型。`04` 文件头通过只读流恢复为 PyTorch zip 头，不修改权重文件。模型依赖固定 v4 底模、vocoder、CN-HuBERT、RoBERTa 和 G2PW 资源。

| 真实负例（均另存副本） | 结果 |
| --- | --- |
| rank 32 张量配 rank 16 元数据 | `LORA_STRUCTURE_INVALID`，报告 LoRA 张量尺寸不匹配 |
| 删除 `bridge.0.weight` | `LORA_STRUCTURE_INVALID`，报告缺少分支 |
| 张量不变但包内采样配置改为 22050 Hz | `SOVITS_AUDIO_CONFIG_MISMATCH: sampling_rate` |
| 把底模依赖路径指向不存在文件 | `MODEL_FILE_CHANGED`，明确指出缺失底模路径 |
| 将真实 GPT 与 SoVITS 路径对调 | `GPT_ARCHITECTURE_MISMATCH` |

五项全部拒绝；原 GPT SHA256 `b3fb4f0553ed54f88d564f0ae6d9d3c60140efa2de4d8ceee5be6980f219e648`、原 SoVITS SHA256 `101c4911eb2e662dd5f833334f733bf3754aef4646dc7ba6bf6bf73f8083b29d` 前后不变。实际请求、结果与负例文件保留在 `data/diagnostics/p07-model-*`，机器可读证据见 [P07_MODELS.json](P07_MODELS.json)。

## 发布与删除回归

```powershell
.\.venv-api\Scripts\python.exe -m pytest tests/test_models_exports.py tests/test_model_worker_cpu.py -q --basetemp data/pytest-p07-models-final
```

**16 passed in 12.68s**：15 项 I/O/发布/引用测试，另 1 项真实 Torch CPU 解析负例合同。小型字节夹具的模拟结构校验不作为真实模型可推理证据。

覆盖：完整发布和独立副本、请求幂等、失败不注册半包、同版本重试、源文件改变拒绝、可信导入与缺权重拒绝、导入后外部编辑不影响快照、活跃任务和晚到引用保护、删除预览后字节改变拒绝、删除只操作管理副本、删除后的快照禁止复活、派生版本与有效恢复点保护、参考音频所属数据保护、未提交回收移动的启动恢复，以及导出快照对周期恢复点的保护。

## 独立审查修复

1. **退休竞态**：重试在事务外读取快照后，模型可能被删除。现在入队写事务重新核 `retired`，禁止用旧内存/磁盘快照覆盖退休状态；回归模拟该交错并验证不产生新任务。
2. **发布中断时默认参考遗漏**：目录原子发布后、媒体登记前退出，旧恢复分支可能注册缺少 audio ID 的默认参考。现在正常和恢复共用登记逻辑，核对包内参考路径及冻结哈希/文本/语言，补齐媒体登记；参考丢失则拒绝 READY。
3. **校对草稿依赖**：修改标注产生的草稿继续引用父版本文件。现在未发布草稿的父版本，以及所有仍使用父片段路径/媒体 ID 的草稿（含已发布只读草稿）均阻止删除父数据。
4. **包内加载配置**：除张量与 rank，CPU 校验也对照锁定 v4 的模型配置及 filter/hop/window/sampling-rate/speaker/segment 参数，防止权重尺寸正确却按包内另一套参数加载。真实改采样配置负例被拒绝，正常真实权重对再次 PASS。

Windows 文件句柄阻止目录发布、原快照成功重试、真实模型副本删除及原始权重保护的主机验收记录位于 `data/p07-management.json`；真实模型 GPU 试听与训练控制证据由 P07 主报告统一归档。
