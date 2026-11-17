# P08 真实浏览器验收

**PASS：7 项检查通过。** 2026-11-17 19:21:09–19:21:18（Asia/Hong_Kong），对既有 `http://127.0.0.1:7867` 执行真实页面操作。Playwright 1.62.1 从本机 Codex Runtime 只读加载，Edge 157.0.4341.60 headless；使用独立 profile，未连接用户浏览器。

## 真实操作与结果

1. 在“语音合成”页选择官方模型，选择本地 WAV 文件。缺少准确文本时上传按钮禁用；填写片段实际文本后，真实上传并设置默认参考，再通过“已管理参考”下拉选择并设置一次。
2. 上传素材来自已发布版本 `76b5a08af4ca471f9c6fa9c903347e69` 中的 8.78 秒片段，复制到浏览器验收目录再上传；新参考 `b4f0cfb9d1b8458595f6499f277ffdf5`、音频 `522f42ff72b546d0a535b6b9f3b9e6c4`，48 kHz 单声道。默认参考的准确文本、语言、时长与音频播放器正常显示，播放事件与时间推进通过。
3. 真实点击停用原逻辑选择、启用官方模型，页面分别显示逻辑启用、本应用 GPU 任务和实际驻留模型。没有把 `gpu_jobs=[]` 描述为整张显卡空闲，另显示包含外部程序占用的设备可用/总显存。
4. 从历史下拉读取比较 A `d5f42268-4077-4e74-a360-cd73501bebfa`，正确提示其来源模型默认参考已更换；读取比较 B `e2426a04-7e35-40d3-a034-91c413a1df19`，不因当前下拉查看官方模型而误报 B 的参考失效。
5. 播放既有真实 GPU 比较 B 的两份结果：自训任务 `7397f63ad17f43d1b0fb7c98f9313bbf` 为 2.84 秒，官方任务 `44852e0b7cf4438fb5b2f695796f3c2b` 为 2.88 秒，两者均为原生 48 kHz；HTML audio 均 readyState=4，触发 `play / loadedmetadata / canplay / playing`，播放位置推进后暂停。
6. 桌面及 390×844 窄屏均通过，窄屏 document.scrollWidth=390，无横向溢出。
7. 验收后恢复原启用模型 `a974c5da69695d0aba3477bff5c7b768`，29 个任务 ID 前后不变，**未启动新的训练、probe 或 compare GPU 任务**。仅官方模型默认参考被授权更新；三个已发布训练版本的默认参考保持原状。源片段 SHA256 前后相同：`4aded8d5c1da9a0b3fc212de72a1c11f42be2907736cbd5e777671f1365b1cc6`。

两份结果的 conditions fingerprint 均为 `9421e38b165d762ea835673e52693f61151c606ce9172822d0a1a992e94f84fd`。实际参数：seed=42、sample_steps=32、top_k=5、top_p=1、temperature=1、speed_factor=1、repetition_penalty=1.35、fragment_interval=0.3。两次实际生成由 root 的 GPU 验收先行完成，本次浏览器只读取并播放这些结果。

音频播放器以静音方式验证解码与播放事件，**没有人工主观听音，不能用于评价音质或相似度**。

## 错误与修正记录

- 最终运行无 console.error、pageerror、HTTP ≥400 或被拦截的越权请求。记录 1 条本地 `blob:` 文件预览请求 `net::ERR_ABORTED`，来自本地预览生命周期终止；没有服务器 API 或音频加载失败。
- 首次浏览器运行在成功上传后遭遇系统 Edge 的 inspector 响应缓存清除，脚本无法读取已完成响应体。应用没有报错；脚本现等待页面成功提示，再通过只读接口核对实际已登记参考。该失败运行及新建的独立参考保留，不回滚或删除用户数据。
- 视觉审查发现旧比较的“参考改变”提示错误地绑定当前下拉查看的模型；已改为比较来源模型的当前默认参考，并以 A/B 历史记录真实复验。最终运行新增 1 份参考；本阶段浏览器尝试共新增 3 份独立参考，最终官方默认参考为上文记录。

## 文件与复跑

- `scripts/validate_p08_browser.cjs`：真实上传、逻辑启停、历史比较与播放测试，设置恢复在异常清理中也会尝试执行。只允许参考上传、官方默认参考和逻辑选择写入；GPU 提交接口被拦截。
- 执行前确保本地服务已启动且已有真实比较，可指定 `DICTIONFORGE_BROWSER_MODEL=official` 和 `DICTIONFORGE_BROWSER_COMPARISON=e2426a04-7e35-40d3-a034-91c413a1df19`，再运行 `node scripts/validate_p08_browser.cjs`。
- 完整记录：`docs/evidence/P08_BROWSER.json`，源记录 `data/p08-browser/2026-11-17T11-21-09-237Z/report.json`，最新指针 `data/p08-browser/latest.json`。
- 4 张截图位于同一运行目录：`01-reference-upload.png`、`02-model-enabled.png`、`03-comparison-results.png`、`04-narrow-synthesis.png`；SHA256 在 JSON 中。
- 前端 `pnpm run build` 通过；`node --test frontend/tests/*.test.mjs` 共 3 项组件回归通过。

本阶段仅实现固定短句效果试听，未提前实现 P09 自由文本合成。
