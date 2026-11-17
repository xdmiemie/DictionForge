# P09 真实浏览器验收

结果：**PASS，10 项检查**。执行时间 2026-11-17 11:51:16–11:51:22 UTC（香港时间 19:51）。服务为 `http://127.0.0.1:7867`，由主任务启动。

使用本机已有 Playwright 1.62.1 和 Microsoft Edge 157.0.4341.60，无头模式，独立 profile 与临时目录均在 `data/p09-browser/2026-11-17T11-51-16-441Z`。没有连接用户浏览器。复跑入口：`node scripts/validate_p09_browser.cjs`，先完成 `data/p09-http.json` 中的四组真实 GPU 输出。

## 真实结果与行为

| 检查 | 结果及实际记录 |
| --- | --- |
| 中文输入及参数 | 目标语言固定中文普通话，编辑文本、断句、语速和种子不改变服务端冻结文本。输入包含 `🌙`；代码点截取另有组件测试。 |
| 单句播放 | `d0dcdf7654654c7b8bb4ed514c62728e`，1 句，3.60 秒，48 kHz，收到真实 `play`/`playing` 事件。 |
| 重复句播放 | `6b81ed20c2544475813973878e0bd687`，3 句，8.32 秒，48 kHz。第 1、3 句文本相同但 sentence_id 不同。 |
| 按行播放 | `faac676251e249428b6c3a7f5a5fcefd`，2 句，8.00 秒，48 kHz，原始换行与冻结文本一致。 |
| 不断句播放 | `1411e75b99634c35a7d4ca357adbd264`，1 句，4.64 秒，48 kHz。 |
| 定位与同步 | 点击第 2 句定位到完整音频约 3.088 秒；高亮原文代码点 `[17,27)`；独立第 3 句播放会暂停完整音频；再选择第 2 句会暂停第 3 句音频。真实播放跨过第 1/2 句边界后高亮同步，不注入模拟 `timeupdate`。 |
| 刷新恢复 | 刷新后恢复服务端 current，同时本地待朗读文本仍为独立编辑稿。 |
| 无效请求保留结果 | 通过真实按钮提交 `？！……`，实际 HTTP 422 / `INVALID_TEXT`；原 current、冻结原文和音频均保留，之后再次播放成功。 |
| 窄屏 | 390×844，文档 scrollWidth = viewportWidth = 390，无横向溢出。 |
| 状态与异常 | 验收前后任务 ID 列表、current ID 和音频 SHA-256 完全一致，无新增有效 GPU 作业；无 pageerror，无非预期 HTTP/console 错误。 |

本次浏览器只读取主任务已经生成的音频，唯一写请求是被明确授权的无效文本请求；没有修改模型、参考或当前结果。浏览器音频测试设为静音，验证真实资源解码、播放器时钟和事件，**不代表人工主观音质试听**。

## 错误与范围说明

- 预期记录 1 次 `/api/tts` HTTP 422，及 Edge 对应的资源加载 console error；没有将该错误隐藏或误计为零。
- 记录 7 次音频请求 `net::ERR_ABORTED`，发生于切换历史结果/播放器或刷新时；对应资源后续均实际播放成功。原始日志保存在 JSON。
- 浏览器完成正常结果与无效输入路径；失败阶段/第几句/失败原文定位由 `frontend/tests/speech.test.mjs` 使用真实组件渲染验证，不冒充真实 GPU 故障验收。
- 本阶段提供当前结果与已有合成任务查看。作品保存、临时产物回收、回流数据集均留待 P10/P11；按用户最新指令本轮停在 P09。

## 证据

- 复跑脚本：`scripts/validate_p09_browser.cjs`
- 原始机器报告：`data/p09-browser/2026-11-17T11-51-16-441Z/report.json`
- 仓库内副本：`docs/evidence/P09_BROWSER.json`
- HTTP/GPU 来源：`data/p09-http.json`，验收时 SHA-256 `bb4adc8e8d0407467d1b463043f455fc11a3aa90f56b7d2872d5cf04a81c55e7`
- 截图均在上述 run 目录，路径和 SHA-256 逐一记录于 JSON：
  - `01-input-and-frozen-result.png`：编辑输入与冻结结果。
  - `02-repeated-sentence-selection.png`：重复句、独立音频、原文高亮及完整播放器定位。
  - `04-lines-result.png`、`05-none-result.png`：按行/不断句的真实结果。
  - `06-invalid-request-preserved.png`：真实 422 与保留的结果。
  - `07-narrow-text-synthesis.png`：390 px 窄屏。

已逐张重点检查重复句结果与窄屏截图，文字、控件、高亮及错误说明均可见。正式前端构建通过；`node --test frontend/tests/*.test.mjs` 共 4 项通过，包含代码点范围、PCM 半开区间和失败定位渲染。
