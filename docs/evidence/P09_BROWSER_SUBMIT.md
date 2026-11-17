# P09 中文页面有效提交到真实 GPU 验收

结果：**PASS，5 项检查**。执行时间 2026-11-17 11:59:32–11:59:57 UTC（香港时间 19:59）。本次补验真实中文页面的「生成语音」按钮，不以直接 HTTP 创建任务代替 UI 提交。

## 本次唯一有效请求

- 任务 / 新 current：`70f2d6554c8548afa3d8bab86a1effba`，最终 `COMPLETED`。
- request_id：`3ddc2089-6a25-4f5f-acb2-1e3da96feb67`。
- 原文：`你好，今天我们一起去公园散步。`
- 模型：`a974c5da69695d0aba3477bff5c7b768`；沿用默认参考 B，fingerprint `c2d11d71752b22529cd9156703a308ba3c3fdc441919a1a26a0927ebb192bfce`。
- 中文 `zh`，标点断句；**语速 1.15、种子 2026、采样步数 16**。
- 其他参数：top_k 5、top_p 1、temperature 1、repetition_penalty 1.35、fragment_interval 0.3。

## 实际检查

1. 在真实页面填写文本和三个非默认参数，点击「生成语音」，实际 POST 被受理，状态从 QUEUED 转为 RUNNING，再到 COMPLETED。浏览器网络守卫只放行这一个精确匹配的有效 POST。
2. 受理后把输入框改为 `这是一条编辑中的新句子，已受理的语音仍使用原文。`。服务端冻结 `text/source_text`、模型、参考 fingerprint、全部参数仍与原请求完全一致；页面冻结原文没有随输入改动。
3. GPU 真实生成后，完整 WAV 和独立句 WAV 均由 Edge 解码播放，收到 `playing` 且播放器时钟实际推进。选择第 1 句，高亮正确原文，sentence_id 与结果相符，actual_seed 为 2026。
4. 刷新页面后，current 恢复为新任务，原文和音频可见，本地输入仍保留独立编辑稿。
5. 验收前后逻辑模型选择及默认参考完整对象不变；任务集合仅增加这一条；原 current `1411e75b99634c35a7d4ca357adbd264` 的音频描述和哈希完全不变。旧四组结果仍保留。

## 产物与实际配置

- 完整音频：`data/runtime/tts/70f2d6554c8548afa3d8bab86a1effba/audio-96f18abdd29448278913e2a984ce339a.wav`
- audio_id：`187e8a863e7646619e45893323aec704`。
- 原生 48,000 Hz，单声道，117,120 样本，2.44 秒，234,284 字节。
- SHA-256：`9d7f6a06d7b54bb052438745ea54cc07e41b8d31831882a79e950b72e0aa7186`。
- 句子：`b3a1e93d77375ab9b15891ca492b51a5`，原文代码点 `[0,15)`，完整音频样本 `[0,117120)`；独立 WAV audio_id `eed4a268e4fd4e1db582c4e919c6f988`，与完整音频哈希一致。
- GPU：NVIDIA GeForce RTX 5080，fp16；模型加载 11.7015 秒，生成 5.2086 秒，合计 17.0281 秒；峰值 allocated CUDA 2,318,460,416 字节，reserved 2,430,599,168 字节。
- 浏览器：Microsoft Edge 157.0.4341.60 / Playwright 1.62.1，无头独立 profile。HTTP 错误、console error、pageerror、被拦截请求均为 0。记录两次 `net::ERR_ABORTED`（旧结果音频替换、刷新时关闭 SSE）；原始日志保留。

音频播放为静音浏览器解码/时钟/事件验证，**没有宣称人工主观音质试听**。本补验没有另做 ASR；此前四组逐句 ASR 报告保持原范围。

## 证据与安全复核

- 脚本：`scripts/validate_p09_browser_submit.cjs`。
- 原始报告：`data/p09-browser-submit/2026-11-17T11-59-32-401Z/report.json`。
- 仓库报告副本：`docs/evidence/P09_BROWSER_SUBMIT.json`，含完整冻结请求、结果、前后对象和截图 SHA-256。
- 持久提交守卫：`data/p09-browser-submit/submission.json`。脚本普通重跑会拒绝第二次有效提交；`--resume` 只验收已有 job，不重新提交。
- 截图目录：`data/p09-browser-submit/2026-11-17T11-59-32-401Z`，含 `01-valid-submission-form.png`、`02-accepted-frozen-text.png`、`03-completed-playable-result.png`、`04-refreshed-current.png`。已查看完成结果截图，高亮、播放器、48 kHz、时长和实际种子均清楚可见。
- Edge context 正常关闭后脚本才写入最终报告并以 0 退出。服务生命周期与最终停机由主任务继续验收。

本次仅补足 P09 有效 UI 提交链路，未实施 P10 保存/作品库或 P11 数据回流。
