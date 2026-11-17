# 真实验收报告

日期：2026-11-17（Asia/Hong_Kong）。PASS 仅用于已执行且满足通过条件的场景。夹具测试不替代真实 GPU、ASR 或浏览器证据。

| 编号 | 场景 | 状态 | 证据/阻塞 |
| --- | --- | --- | --- |
| A01 | 仓库与模型基线 | PASS | origin/default main/空历史已核对；configs/upstream.lock.json、UPSTREAM_AUDIT.md、BASELINE_AUDIT.md |
| A02 | 任务重复请求 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A03 | 断线与刷新 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A04 | 进程失联与构建 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A05 | CUDA 与中文路径 | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A06 | 缺失或损坏的资产 | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A07 | 官方底模与 ASR | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A08 | 扫描混合格式 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A09 | 源录音保护 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A10 | 无效素材与统计 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A11 | 切分正确性 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A12 | ASR 重试和校对 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A13 | 训练清单和分组 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；UI浏览器补验另列BLOCKED |
| A14 | 上游特征 | PASS | docs/evidence/P04_FEATURES.md、P04_HTTP.json、P04_ZERO_BATCH_RESULT.json；40项回归。显存仅加载器，完整训练待P05 |
| A15 | 特征修复 | PASS | docs/evidence/P04_FEATURES.md、P04_HTTP.json、P04_ZERO_BATCH_RESULT.json；40项回归。显存仅加载器，完整训练待P05 |
| A16 | 修改输入使特征失效 | PASS | docs/evidence/P04_FEATURES.md、P04_HTTP.json、P04_ZERO_BATCH_RESULT.json；40项回归。显存仅加载器，完整训练待P05 |
| A17 | 零批次与实际参数 | PASS | docs/evidence/P04_FEATURES.md、P04_HTTP.json、P04_ZERO_BATCH_RESULT.json；40项回归。显存仅加载器，完整训练待P05 |
| A18 | 实际双阶段微调 | PASS | docs/evidence/P05_TRAINING.md、P05_TRAINING.json、P05_HTTP.json；真实S2/S1更新、steps/epochs与单阶段；58项回归 |
| A19 | 累计停止目标 | PASS | docs/evidence/P05_TRAINING.md、P05_TRAINING.json、P05_HTTP.json；真实S2/S1更新、steps/epochs与单阶段；58项回归 |
| A20 | 阶段交接和单阶段训练 | PASS | docs/evidence/P05_TRAINING.md、P05_TRAINING.json、P05_HTTP.json；真实S2/S1更新、steps/epochs与单阶段；58项回归 |
| A21 | SoVITS 暂停并重启 | PASS | docs/evidence/P06_RECOVERY.md、P06_HTTP.json、P06_CHECKPOINT_IO.md；真实暂停重启/受控中断/派生与文件故障验证 |
| A22 | GPT 暂停并重启 | PASS | docs/evidence/P06_RECOVERY.md、P06_HTTP.json、P06_CHECKPOINT_IO.md；真实暂停重启/受控中断/派生与文件故障验证 |
| A23 | 轮内恢复和交接中断 | PASS | docs/evidence/P06_RECOVERY.md、P06_HTTP.json、P06_CHECKPOINT_IO.md；真实暂停重启/受控中断/派生与文件故障验证 |
| A24 | 半写、磁盘满、损坏恢复点 | PASS | docs/evidence/P06_RECOVERY.md、P06_HTTP.json、P06_CHECKPOINT_IO.md；真实暂停重启/受控中断/派生与文件故障验证 |
| A25 | 从较早恢复点派生 | PASS | docs/evidence/P06_RECOVERY.md、P06_HTTP.json、P06_CHECKPOINT_IO.md；真实暂停重启/受控中断/派生与文件故障验证 |
| A26 | 更换数据和权重初始化 | NOT_RUN | 依赖P07模型注册，随P11新数据和来源模型实训补验 |
| A27 | 训练中导出 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A28 | 中止并导出 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A29 | 自然完成自动发布 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A30 | 导出失败重试 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A31 | 外部权重对 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A32 | 模型和数据集管理 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A33 | 默认参考和缓存 | NOT_RUN | 尚未执行 |
| A34 | 固定条件试听 | NOT_RUN | 尚未执行 |
| A35 | GPU 排他和外部占用 | NOT_RUN | 尚未执行 |
| A36 | 单句合成 | NOT_RUN | 尚未执行 |
| A37 | 多句、重复句和断句 | NOT_RUN | 尚未执行 |
| A38 | 无效文本和语言 | NOT_RUN | 尚未执行 |
| A39 | 合成与旧结果 | NOT_RUN | 尚未执行 |
| A40 | WAV、MP3 与 Range | NOT_RUN | 尚未执行 |
| A41 | 永久作品 | NOT_RUN | 尚未执行 |
| A42 | 保存并发和 Windows 句柄 | NOT_RUN | 尚未执行 |
| A43 | 作品筛选与分页 | NOT_RUN | 尚未执行 |
| A44 | 批量删除 | NOT_RUN | 尚未执行 |
| A45 | 单句追加复用文本 | NOT_RUN | 尚未执行 |
| A46 | 多句选择与追加再切分 | NOT_RUN | 尚未执行 |
| A47 | 追加失败和回滚 | NOT_RUN | 尚未执行 |
| A48 | 并发追加和去重 | NOT_RUN | 尚未执行 |
| A49 | 数据谱系与留出集 | NOT_RUN | 尚未执行 |
| A50 | 新版本二次微调 | NOT_RUN | 尚未执行 |
| A51 | 全新环境启动和停机 | NOT_RUN | 尚未执行 |
| A52 | 目标硬件全流程 | NOT_RUN | 尚未执行 |
| A53 | 角色音质 | NOT_RUN | 尚未执行 |
| A54 | 交付与提交 | NOT_RUN | 尚未执行 |
