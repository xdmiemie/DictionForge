# P07 模型发布、导入、管理与真实推理验收

2026-11-17，沿用已锁 GPT-SoVITS v4 / RTX 5080 / Torch 2.9.0+cu128。所有音频来自实际推理，未使用占位波形。短训用于验证链路，音色质量另在 P12 评价。

执行 `scripts/validate_p07.py` 和 `scripts/validate_p07_management.py`，服务由 `.venv-api/Scripts/python.exe -m dictionforge.serve` 启动。详细记录为 `P07_HTTP.json`、`P07_MANAGEMENT.json`。训练、参考、参数、模型哈希与音频产物均可追溯。

| 发布路径 | 训练 / 模型版本 | 实际结果 |
| --- | --- | --- |
| 导出并继续 | run `278c506666fa4876bff2ec0bf0d7d053`；model `042ba87417df5c67b75f9ef27777be74` | S2 u3 冻结，GPT尚未开始，明确使用初始GPT权重；CPU发布时GPU继续更新 |
| 自然完成 | 同run；model `a974c5da69695d0aba3477bff5c7b768` | S2累计40更新、S1累计4更新后自动发布完整对 |
| 中止并导出 | run `cca9eda7641b4436b238c936a9b23ffd`；model `1d9b77725c935da2af1a8f7279e0c7be` | S2 u3保存完整点并停止，运行EARLY_COMPLETED，S1未更新，保留可恢复状态 |

CPU/GPU重叠由实时HTTP轮询记录验证：导出job RUNNING期间训练job仍RUNNING且S2更新继续增长。导出使用独立CPU租约；验证子进程显式禁用CUDA，结构通过和真实GPU试听分开登记。快照及发布包包含两份权重、哈希、底模/rank依赖、阶段来源、训练摘要、默认参考和固定推理配置。临时包不能出现在可选模型中。

固定短句为「你好，欢迎来到声句工坊。」；相同已校对参考，zh / fp16 / seed42 / sample_steps32 / top_k5 / top_p1 / temperature1 / speed1 / repetition_penalty1.35 / cut0 / batch1。三种发布包均通过同一独立GPU推理入口：

| 路径 | GPU测试任务 | 原生采样/样本数 | 生成耗时 | 音频SHA256 |
| --- | --- | --- | --- | --- |
| 中途 | `5cd28d453d3b4474addfce1c97bec49c` | 48000Hz / 168960 | 21.079秒 | `475a69bf7c4d8653d2cfa995de9f350e90082f7f41502015bc2cd7194c66106c` |
| 完成 | `e6dafc6cb472472eb23151a1c3b05234` | 48000Hz / 182400 | 7.876秒 | `b63737c2b75cec3cabf9a2f76506e689184c183b6593ab2ea2c6d404bdaa2d4d` |
| 中止 | `712e81ed370e4ca4ae4e04be0ff583b5` | 48000Hz / 168960 | 6.609秒 | `e1929fc6d8cd6a4391cc85a18e0874199904c8608f62af43c59063c4e988019d` |

三次Torch峰值allocated均2318460416字节，均单声道、非静音，禁用隐式网络和超采样。模型载入耗时单独记录，不混入上述生成耗时。

## 实际失败与同快照重试

从已验证权重对导入独立验收副本 `2b66095bc2565946a309ac2b46e93005`，在其临时发布目录打开不共享删除的真实Windows目录句柄。结构校验后，原子目录发布实际返回WinError32，任务FAILED、模型列表无半成品。关闭句柄后从同一个snapshot重试，任务 `fa9ee16f3a6241a882648f3819a66eda` 成功；snapshot manifest SHA和model version id均不变，重复同request只返回同job，版本只登记一次。

删除该验收副本前显示准确文件清单和引用，删除后原始来源权重哈希不变，已退役snapshot的再次重试被拒绝。未确认可信来源的导入在受理前拒绝。缺依赖、错架构/rank、损坏tensor的CPU结构测试另见模型服务验收报告。

## 删除保护

数据版本 `76b5a08af4ca471f9c6fa9c903347e69` 被恢复点、运行和模型引用，预览显示保护原因，真正DELETE返回409。随后创建独立标注版本副本 `d6da434e25044779ab2b8c3d5bf51417`，只删除预览确认集合；父数据版本、原录音和原权重保持完整。此副本为测试生成，不是用户原始录音。

删除使用持久化移动意图、管理目录内暂存与单文件清理，Windows占用失败保留可重试状态。独立审查补齐了退役快照与重试竞争、发布崩溃后默认参考验证/媒体登记、未发布标注草稿的父音频保护。见 `P07_REVIEW.md`。

回归：111 passed、6 skipped（API环境无Torch的实际循环测试另在worker环境通过）；worker纯合同11项及实际CPU循环6项通过；模型服务15项独立回归通过；前端build及2个组件回归通过。浏览器实测结果单独记录，未把HTTP、CPU或SSR测试冒称浏览器试听。

收尾：实际Playwright/Edge浏览器9项通过，验证已有模型、训练发布状态、删除预览与保护、音频加载/静音播放事件及390px布局。修复两项窄屏溢出；完整报告 `P07_BROWSER.md/.json`。CPU模型专项最后补包内配置校验及真实坏配置副本，合计16项通过，5个真实坏包拒绝且正常包再次PASS，详见 `P07_MODELS.md/.json`。
