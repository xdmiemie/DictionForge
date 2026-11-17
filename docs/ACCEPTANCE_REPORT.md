# 真实验收报告

日期：2026-11-17（Asia/Hong_Kong）。PASS 仅用于已执行且满足通过条件的场景。夹具测试不替代真实 GPU、ASR 或浏览器证据。

| 编号 | 场景 | 状态 | 证据/阻塞 |
| --- | --- | --- | --- |
| A01 | 仓库与模型基线 | PASS | origin/default main/空历史已核对；configs/upstream.lock.json、UPSTREAM_AUDIT.md、BASELINE_AUDIT.md |
| A02 | 任务重复请求 | NOT_RUN | 尚未执行 |
| A03 | 断线与刷新 | NOT_RUN | 尚未执行 |
| A04 | 进程失联与构建 | NOT_RUN | 尚未执行 |
| A05 | CUDA 与中文路径 | NOT_RUN | 尚未执行 |
| A06 | 缺失或损坏的资产 | NOT_RUN | 尚未执行 |
| A07 | 官方底模与 ASR | NOT_RUN | 尚未执行 |
| A08 | 扫描混合格式 | NOT_RUN | 尚未执行 |
| A09 | 源录音保护 | NOT_RUN | 尚未执行 |
| A10 | 无效素材与统计 | NOT_RUN | 尚未执行 |
| A11 | 切分正确性 | NOT_RUN | 尚未执行 |
| A12 | ASR 重试和校对 | NOT_RUN | 尚未执行 |
| A13 | 训练清单和分组 | NOT_RUN | 尚未执行 |
| A14 | 上游特征 | NOT_RUN | 尚未执行 |
| A15 | 特征修复 | NOT_RUN | 尚未执行 |
| A16 | 修改输入使特征失效 | NOT_RUN | 尚未执行 |
| A17 | 零批次与实际参数 | NOT_RUN | 尚未执行 |
| A18 | 实际双阶段微调 | NOT_RUN | 尚未执行 |
| A19 | 累计停止目标 | NOT_RUN | 尚未执行 |
| A20 | 阶段交接和单阶段训练 | NOT_RUN | 尚未执行 |
| A21 | SoVITS 暂停并重启 | NOT_RUN | 尚未执行 |
| A22 | GPT 暂停并重启 | NOT_RUN | 尚未执行 |
| A23 | 轮内恢复和交接中断 | NOT_RUN | 尚未执行 |
| A24 | 半写、磁盘满、损坏恢复点 | NOT_RUN | 尚未执行 |
| A25 | 从较早恢复点派生 | NOT_RUN | 尚未执行 |
| A26 | 更换数据和权重初始化 | NOT_RUN | 尚未执行 |
| A27 | 训练中导出 | NOT_RUN | 尚未执行 |
| A28 | 中止并导出 | NOT_RUN | 尚未执行 |
| A29 | 自然完成自动发布 | NOT_RUN | 尚未执行 |
| A30 | 导出失败重试 | NOT_RUN | 尚未执行 |
| A31 | 外部权重对 | NOT_RUN | 尚未执行 |
| A32 | 模型和数据集管理 | NOT_RUN | 尚未执行 |
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
