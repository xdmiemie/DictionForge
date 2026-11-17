# P08 默认参考与逻辑启用验收

日期：2026-11-17。本报告覆盖 CPU 音频导入、参考冻结、逻辑选择和引用保护；GPU 固定短句对比由 P08 主验收记录覆盖。

## 实现

`references_service.import_reference` 接收本地路径或已登记媒体 ID，要求准确非空中文文本，支持 `zh`。解码后按实际样本数严格检查 3–10 秒、非静音与数值有限性，再写入独立 UUID 目录中的 PCM16、单声道、48000 Hz WAV。源路径与 SHA256 单独保留；来源媒体在复制期间持有租约。替换默认参考只变更模型元数据，不修改任何旧参考或权重。

参考条件指纹为 `sha256/text/language` 的规范 JSON 哈希。文件内容相同但新 UUID 的参考可复用相同条件；音频或文本改变会得到新指纹。模型列表仅把匹配当前默认参考的 GPU 测试标为 PASS/FAILED；替换后显示 NOT_RUN，旧测试继续保留。官方模型的默认参考独立保存在数据库，未设置时不预填。

`enable` 完整校验模型权重及共享资产，再校验参考哈希、格式、时长、文本与语言；状态和幂等收据在同一数据库事务提交。失败不切换之前有效的选择。逻辑启用不等于 GPU 常驻：推理任务仍由 GPU 队列临时加载。列表和 selection 轮询只检查元数据、文件存在/尺寸及小型参考文件，避免每次读取数 GB 权重。

启用模型受到删除保护，切换或关闭后释放该保护。媒体租约传播到所属模型/数据版本。旧参考暂留，供已冻结任务继续使用，后续统一清理。

## 自动化回归

```powershell
.\.venv-api\Scripts\python.exe -m pytest tests/test_references_service.py tests/test_models_exports.py -q --basetemp data/pytest-p08-references-complete
```

结果：**28 passed in 14.09s**，包含 13 项参考/选择测试和 15 项既有模型发布/引用回归。

- 真实 ffmpeg 解码及 soundfile 校验：32 kHz、双声道 PCM24 测试音转换为 48 kHz、单声道 PCM16，原文件哈希不变。
- 2.99 秒、10.01 秒、静音、空文本、不支持语言、损坏音频和媒体哈希变化均拒绝，未生成可用参考或改变原选择。
- 重复 request ID 返回原结果；不同 request 生成不同 UUID 与路径，不覆盖旧文件。
- 替换模型默认参考后，已受理的 model_probe 请求仍逐字段保留旧参考路径、文本和哈希。
- 默认参考缺失/改变时不能启用；轻量 selection 不调用完整模型权重哈希。
- 逻辑启用、切换、关闭的删除保护，以及媒体复制租约对所属数据版本的保护均验证。
- 官方模型默认参考持久化与 GPU 状态按参考条件隔离，历史 PASS 不覆盖新参考的 NOT_RUN/FAILED。

测试波形只用于格式和边界合同，不作为真人语音或 GPU 音质验收。

补充 HTTP/协议边界定向执行：

```powershell
.\.venv-api\Scripts\python.exe -m pytest tests/test_references_service.py -q --basetemp E:\DictionForge\data\pytest-p08-reference-http-native
```

最终参考服务 **20 passed in 8.33s**。新增坏字节上传的真实 HTTP 422、incoming 临时文件清理、原源不变、soundfile 错误转换、URL/UNC 拒绝和真实 MP3/FLAC 导入。ffprobe 与 ffmpeg 都仅开放 `file,pipe` 协议，参考格式限制为本地 WAV/MP3/FLAC；解析失败统一为 `REFERENCE_DECODE_FAILED`，诊断最多 400 字符。TestClient 的异步线程在默认沙盒中停滞，因此该定向命令在已授权本机权限下执行；测试只使用独立夹具，没有启动 GPU 或主监督器。

独立审查实证并修复了上传路由取消期间的文件生命周期问题：原先 `asyncio.to_thread` 工作线程仍运行时，路由的 finally 会提前删除 incoming。当前路由将导入线程封装为任务并 shield，收到取消后先等待导入结束，再清理输入并传播取消。主任务新增 `tests/test_reference_upload_lifetime.py`，真实线程暂停/路由取消验证输入一直保留到读取完成；该项与试听输出失败登记共 4 项定向测试通过。原复现实验保留于 `data/diagnostics/p08_review_repro.py`。参考服务自身不删除外部源文件。

## 真实录音独立导入

```powershell
.\.venv-api\Scripts\python.exe scripts/validate_reference_import.py data/models/09e42df26bf30b98cea3880e/1d9b77725c935da2af1a8f7279e0c7be/manifest.json data/diagnostics/p08-real-reference
```

使用 P07 模型包内经过校对的真人角色参考，长度 **8.78 秒 / 421440 个样本**。在独立数据根导入两次，得到 `5dcc0c6509c54c0891b806ad3a757f08` 与 `eeba4c5bb70e4d439516283a7a988555` 两个不同参考 ID；相同 request 的重复调用完全幂等。两份产物都是 48000 Hz、mono、PCM16，且原参考与新副本 SHA256 均为 `4aded8d5c1da9a0b3fc212de72a1c11f42be2907736cbd5e777671f1365b1cc6`。

没有更改主应用选择、原录音或原模型参考，没有使用 GPU。真实产物和完整来源保留在 `data/diagnostics/p08-real-reference/`，摘要见 [P08_REFERENCES.json](P08_REFERENCES.json)。
