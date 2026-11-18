# P10 永久作品服务证据

本页记录保存、列表与删除服务的 CPU / 文件 I/O 验证。样本是测试生成的 48 kHz PCM 波形，不是 GPU 合成或主观试听证据。主任务另执行 P09 真实合成结果的 HTTP 保存、播放、Range、重启及删除验收。

## 保存边界

- `POST /api/tts/{id}/save` 仅受理已成功、已登记媒体的 TTS 完整音频；受理写事务同时冻结来源、标题、格式、模型与文本、参考、实际参数及谱系，获取源媒体的 job lease。保存进入独立 CPU 队列 `speech_save`，不占 GPU 租约。
- 相同请求 ID / 输入返回同一任务和作品 ID，不重复登记；不同输入使用同请求 ID 被拒绝。保存不更改 `tts_current`。
- 每个作品在 `data/saved_speech/<UUID>/` 内保存独立的 `source.wav`。WAV 复制后完整解码，必须为单声道、PCM16、48 kHz，SHA-256 必须与冻结源一致。
- MP3 调用 FFmpeg `libmp3lame -b:a 320k -ar 48000 -ac 1`。使用 ffprobe 核实 MP3 / 320000 bit/s / 48 kHz / 单声道，再完整解码为 PCM16，样本数必须与独立源 WAV 相同。MP3 为有损编码，不要求 PCM 值与 WAV 相同。
- 写入先在该作品的 `.staging` 目录完成，验证后写 manifest，再以目录改名发布并事务登记音频。目录发布后、数据库登记前中断，可从原 manifest 恢复同一个 ID；验证来源哈希与路径身份。已删除作品的保存重放明确拒绝，不复活文件。
- 源 job lease 在保存排队、复制、编码、校验及登记期间保持；终态后由媒体生命周期对账释放。保存失败、磁盘写入失败或取消均不改变原 current；原始源文件仍保持哈希。
- 保存的音频播放使用独立 `audio` / `wav` 媒体。参考和句子信息是历史来源元数据，其中的原 TTS 路径不是永久作品的播放依赖。

## 列表和删除

- 列表按 `(created,id)` 倒序，可按模型版本及标题 / 原文筛选；每页 1–10 条，超过上限拒绝。
- 本页选择通过确切 IDs 提交；全部筛选结果在 preview 时冻结完整集合、数量、文件清单、哈希和 10 分钟到期 token。之后新增的同筛选作品不会进入该次确认。
- 确认逐条返回 `deleted`、`already_deleted` 或 `protected`。写事务内重新检查当前文件清单、文件哈希及作品 / 音频的活跃租约。播放、下载或任务仍持有引用时不移动作品。
- 删除先持久写 `PREPARED` 意图，随后把严格管理目录移动到 trash，在同数据库事务中标记作品及媒体为已删除，最后清理 trash。提交前崩溃恢复原目录；提交后清理失败保留 `PURGE_PENDING` 重试。
- 删除确认与启动 / 定时对账共用 `saved-deletion.lock`，防止对账恢复正在操作的意图。Windows 目录移动失败时保持作品可读并返回 protected；trash 文件占用时保持已删除状态和 pending 意图，句柄释放后继续清理。

## CPU 验证

```powershell
.venv-api\Scripts\python.exe -m pytest tests/test_saved_speech.py -q --basetemp data/pytest-p10-saved-final
```

结果：**12 passed in 13.44s**。

覆盖：独立 WAV 哈希 / 重启读取 / 幂等；实际 FFmpeg 320 kbps MP3 编码与完整解码；编码失败及取消；目录发布后的登记中断恢复；12 条作品的 10+2 分页、冻结全筛选集合后新增第 13 条、引用保护与释放重试；同创建时间按 ID 排序和本页删除；清单变更、过期 token、路径越界；模拟 Windows rename / trash unlink 锁；模拟事务中断并启动恢复；模拟磁盘满；删除后重放不复活；对账不触碰正在确认的操作。

模拟故障明确为 I/O 合同测试，不冒充实际磁盘已满或真实 Windows 句柄验收。作者未运行 GPU、未修改原始录音、未提交 Git。
