# 真实验收报告

日期：2026-11-18（Asia/Hong_Kong）。PASS 仅用于已执行且满足通过条件的场景。夹具测试不替代真实 GPU、ASR 或浏览器证据。

| 编号 | 场景 | 状态 | 证据/阻塞 |
| --- | --- | --- | --- |
| A01 | 仓库与模型基线 | PASS | origin/default main/空历史已核对；configs/upstream.lock.json、UPSTREAM_AUDIT.md、BASELINE_AUDIT.md |
| A02 | 任务重复请求 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A03 | 断线与刷新 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A04 | 进程失联与构建 | PASS | tests/test_jobs.py（4 passed）；docs/evidence/P01_UI.md；pnpm run build |
| A05 | CUDA 与中文路径 | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A06 | 缺失或损坏的资产 | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A07 | 官方底模与 ASR | PASS | docs/evidence/P02_ENVIRONMENT.md、P02_ASSETS.md、P02_CUDA.json、P02_ASR.json、P02_TTS.json；16项回归通过 |
| A08 | 扫描混合格式 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
| A09 | 源录音保护 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
| A10 | 无效素材与统计 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
| A11 | 切分正确性 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
| A12 | ASR 重试和校对 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
| A13 | 训练清单和分组 | PASS | tests/test_datasets.py、test_asr_contract.py；docs/evidence/P03_DATASET.json、P03_DATASET_INITIAL.json、P03_ASR.md；完整页面已由P12_BROWSER.json补验 |
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
| A26 | 更换数据和权重初始化 | PASS | P11_FEEDBACK.md、P11_HTTP.json：两种初始化实际张量与首步核对、新优化器计数为零 |
| A27 | 训练中导出 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A28 | 中止并导出 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A29 | 自然完成自动发布 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A30 | 导出失败重试 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A31 | 外部权重对 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A32 | 模型和数据集管理 | PASS | docs/evidence/P07_EXPORT.md、P07_HTTP.json、P07_MANAGEMENT.json；三种真实GPU推理/同快照重试/导入与保护删除 |
| A33 | 默认参考和缓存 | PASS | docs/evidence/P08_EFFECTS.md、P08_HTTP.json、P08_GPU.md、P08_BROWSER.md；固定条件真实推理/参考失效/租约与受控外部占用 |
| A34 | 固定条件试听 | PASS | docs/evidence/P08_EFFECTS.md、P08_HTTP.json、P08_GPU.md、P08_BROWSER.md；固定条件真实推理/参考失效/租约与受控外部占用 |
| A35 | GPU 排他和外部占用 | PASS | docs/evidence/P08_EFFECTS.md、P08_HTTP.json、P08_GPU.md、P08_BROWSER.md；固定条件真实推理/参考失效/租约与受控外部占用 |
| A36 | 单句合成 | PASS | P09_SYNTHESIS.md、P09_HTTP.json、P09_BROWSER.md：真实v4原生48k、浏览器解码播放 |
| A37 | 多句、重复句和断句 | PASS | P09_WORKER.md/.json、P09_ASR.json、P09_BROWSER.md：4组7句PCM逐样本与原文范围一致、真实ASR与播放同步 |
| A38 | 无效文本和语言 | PASS | P09_HTTP.json、P09_BROWSER.md：7种HTTP422均保留current，页面真实非法标点请求后仍可播放 |
| A39 | 合成与旧结果 | PASS | P10_HTTP.json、P10_LIFECYCLE.md：有效新请求退休旧结果；取消/失效410/原文保留，非法请求保留current |
| A40 | WAV、MP3 与 Range | PASS | P10_HTTP.json、P10_SAVED_SPEECH.md：真实48k WAV/320kbps MP3完整解码；206字节/416/HEAD/download，HTTPfinally释放引用 |
| A41 | 永久作品 | PASS | P10_HTTP.json restart_validation：独立作品跨新任务和完整服务重启，哈希/解码/Range一致 |
| A42 | 保存并发和 Windows 句柄 | PASS | P10_HTTP.json、P10_MEDIA_LIFECYCLE.md：真实排队保存、Windows句柄pending→release→deleted，13项专项通过 |
| A43 | 作品筛选与分页 | PASS | P10_HTTP.json、P10_BROWSER.md：真实页面模型/标题筛选，14件作品10+4分页，翻页及筛选清除选择 |
| A44 | 批量删除 | PASS | P10_HTTP.json、P10_BROWSER.md：精确单条/本页/跨页确认集合，播放保护；HTTP及浏览器预览后新增不误删，确认框保留冻结集合 |
| A45 | 单句追加复用文本 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A46 | 多句选择与追加再切分 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A47 | 追加失败和回滚 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A48 | 并发追加和去重 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A49 | 数据谱系与留出集 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A50 | 新版本二次微调 | PASS | P11_FEEDBACK.md、P11_HTTP.json、P11_BROWSER*.json；实际 GPU/ASR/页面，故障注入合同单独标明 |
| A51 | 全新环境启动和停机 | PASS | P12_DELIVERY.md、P12_BROWSER.json、P12_LISTENING.json、P12_*；提交后审计/推送结果在data记录 |
| A52 | 目标硬件全流程 | PASS | P12_DELIVERY.md、P12_BROWSER.json、P12_LISTENING.json、P12_*；提交后审计/推送结果在data记录 |
| A53 | 角色音质 | PASS | P12_DELIVERY.md、P12_BROWSER.json、P12_LISTENING.json、P12_*；提交后审计/推送结果在data记录 |
| A54 | 交付与提交 | PASS | P12_DELIVERY.md、P12_BROWSER.json、P12_LISTENING.json、P12_*；提交后审计/推送结果在data记录 |

全部54项均有相应执行证据。A53采用用户实际试听后的明确确认；自动播放仅作解码验证。P12新环境使用已有已校验模型/依赖缓存，不等于重装Windows；实际边界和下载失败修复见 evidence/P12_DELIVERY.md。

2026-11-18 用户追加关联删除要求后，A32的管理界面改为先列出关联模型、数据和任务，再一并确认删除；历史引用不再一律阻止。原始录音、共享资产和独立作品保留，活动任务及实际文件读写仍保护。新版验收见 `evidence/UI_CASCADE_DELETE.json`：独立数据库4项真实页面删除通过、API全套262 passed/7 skipped、最终关联删除专项15 passed、前端5项组件及构建通过。上表P00–P12证据保留为交付时记录。
