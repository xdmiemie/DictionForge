# P07 独立代码审查

日期：2026-11-17。范围：`exports.py`、`models.py`、`references.py`、发布/删除相关路由与前端。未修改其他代理负责的后端模块；以下修复由 upstream 代理实施并由本审查复核。

## 发现与复核

1. **删除与失败导出重试的竞争**：旧实现先在事务外读取 `retired`，随后提交时可能用旧快照覆盖删除事务写入的退役标记。已在 `_submit_snapshot.accepted` 的同一写事务中复核当前快照，拒绝已退役版本，保留当前数据库快照。新增交错事务回归通过。
2. **发布崩溃恢复遗漏参考素材登记**：`staging.replace(final)` 后、数据库提交前退出时，旧恢复分支仅检查权重/依赖，没有验证默认参考或补齐媒体 ID。正常路径与恢复路径现共用 `_register`，核对包内参考路径、哈希、文本、语言并补媒体登记；缺失参考禁止注册。恢复/损坏两种回归通过。
3. **标注草稿引用未保护父数据版本**：`edit_version` 使用父版本音频路径，旧引用扫描忽略 draft，导致删除父版本后草稿无法播放或发布。隔离实际调用曾返回 `draft_uses_original=true, deletable=true, references=[]`。修复后原夹具重跑返回 `deletable=false` 和 draft 引用；未发布及已发布只读草稿的实际文件引用均受保护。复现夹具保留在 `data/review-draft-protection/a6682627a85c4f66adee4966f55ef509`，未对该夹具执行删除。

独立执行：`.venv-api/Scripts/python.exe -m pytest tests/test_models_exports.py -q --basetemp=data/pytest-model-review`，**15 passed**。这些是实际文件/SQLite 事务合同检查，模型结构子进程使用测试替身，不能作为真实 Torch 或 GPU 推理通过的证据。

前端追加隐藏共享官方底模删除入口；其管理归属预训练资产。`pnpm run build` 通过；`node --test tests/*.test.mjs` **2 passed**，覆盖校验状态分列、初始化阶段来源、引用保护禁删、官方模型禁删及空指标安全渲染。

## 后续播放器引用边界

当前音频 GET 路由返回 `FileResponse`，没有为响应生命周期获取媒体引用。已通知 root 在播放器/保存/回收阶段统一补齐租约，覆盖响应打开前的删除竞争、播放中删除、连接中断与释放。Windows 文件占用造成的删除回滚不能替代应用层引用保护。

## P12 浏览器验收工具调查

仅只读检查，**未启动浏览器**：

- Codex 本机运行时提供 `playwright` **1.62.1**；Node `require()` 成功。模块位于运行时 `dependencies/node/node_modules/playwright`，可由 `process.execPath` 的相邻目录定位。
- Playwright 默认 Chromium 1234 的预期可执行文件不存在。
- 系统 Edge 可执行文件存在：`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`，版本 **157.0.4341.60**。

可在 P12 使用现有 Playwright，指定上述 Edge `executablePath`、`headless: true`，创建独立临时浏览器上下文测试本项目。建议记录真实 DOM 操作、网络失败和页面异常，覆盖中文双标签、任务事件/恢复、模型版本、合成与结果保持、保存下载、回流跳转；检查 HTML 音频元素加载/播放事件与时长，核对下载文件采样率和哈希，保存桌面及窄屏截图。此方案尚未实测，不能据此声称浏览器验收通过。无头播放事件与音频文件分析也不能证明主观音质，主观听音应另行如实记录。
