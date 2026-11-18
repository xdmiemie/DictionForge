# P10 持久媒体租约与临时音频回收专项

日期：2026-11-18。实现：`backend/dictionforge/media_lifecycle.py`；合同测试：`tests/test_media_lifecycle.py`。本专项不执行 GPU、不修改原始录音、不执行 Git 提交。

## 最终验收结果

最终主任务授权全套测试的 `data/p10-final-tests.xml` 已独立读取：共 228 项，**222 passed / 6 skipped / 0 failures / 0 errors**，96.936 秒。其中媒体生命周期专项 **13 项全部通过、没有跳过**，包含真实 Windows 无共享权限句柄、仅允许共享读取的句柄，以及实际 junction / 重解析路径拒绝。

早先沙箱内专项结果为 12 passed / 1 skipped（不能创建 junction）；随后该单项升级工具调用被中断，未取得结果。最终全套授权运行已实际执行并通过此项，因此不把早先跳过计为通过，也无须重复运行。

主任务实际 HTTP / GPU 验收独立记载于 `P10_HTTP.json`、`P10_LIFECYCLE.md`：排队保存任务在旧结果退休后继续保护源文件；真实 Windows `CreateFileW` 句柄使清理进入 `PURGE_PENDING`，关闭句柄后自动变成 `DELETED`；旧音频及再次保存返回 410，历史原文仍可查；两个独立永久作品跨服务重启后哈希、解码、Range 和下载头仍通过。这里仅复核已有报告，不另行启动 GPU。

## 持久接口与竞争条件

- `acquire_browser(jobs,audio_id,ttl=90,scope='owner',db=None)` 返回显式 token、owner 和 Unix `expires_at`。默认按 owner 保护完整 TTS 及其所有句音频；`heartbeat` 续期，`release` 幂等释放。过期 token 不能通过心跳复活。
- `acquire_http(jobs,audio_id,db=None)` 无时间到期，记录 API PID 与进程启动时间；实际响应全程持有，root 的响应封装在正常完成、HEAD、Range 错误和断连的 finally 中释放。重启对账仅按进程身份回收失效 HTTP 租约。
- `acquire_job(jobs,audio_ids,owner_job,db=None)` 必须关联数据库中的排队或运行任务。保存受理的 `on_accept` 同一 SQLite 写事务冻结请求并取得租约；不设 TTL，直到任务终态才对账释放。运行中的 job lease 不允许被通用 release 提前撤销。
- `active_leases(...,owner=... / media_id=...,db=...)` 为作品删除和媒体引用检查提供同一事务内查询。`MediaGone` 由 root 映射 HTTP 410。
- 所有受理与清理决定共用 `BEGIN IMMEDIATE` 写锁。测试实际开启两个线程：保存受理先持锁时，后来的清理保留源；持久清理意图先成功时，后来的播放和保存均被拒绝，失败受理不留下半个任务。

## 回收范围及崩溃恢复

- 成功历史结果只在有效新任务受理时由 `retire_previous` 显式退休；单独重启不自动清空 P09 历史。当前成功结果始终受保护。失败、取消、中断任务自动登记退休，但活跃任务或租约仍阻止清理。
- 仅操作严格限定的 `data/runtime/tts/<owner>/` 文件。登记媒体必须属于该 owner、非 external、路径和类型正确；未登记产物只接受已知 worker 的 `audio-<uuid>.wav` / `sentence-<uuid>-<index>.wav` 及其 `.part` 名称。JSON、原文记录、未知文件、外部原始录音和 `data/saved_speech` 不在临时清理范围。
- 逐层拒绝符号链接和 Windows reparse point / junction；清理意图再次校验 source/trash 路径、已知哈希、文件身份和 owner。跨 owner 或 external 路径别名受保护，不绕过到目标目录。
- 第一步先提交持久 `media_purge` 意图并把媒体标成 `PURGE_PENDING`，阻止新引用；第二步在写锁内原子移动到 `data/runtime/trash/tts/<owner>/<intent>/`，提交媒体 `DELETED`；第三步才 unlink 回收文件。历史正文及推理参数始终保留。
- 真实 Windows 文件占用时记录错误与待清理状态，不报告成功删除。启动及定时对账重试。合同测试还模拟“已移动、数据库提交前崩溃”，重启从原有持久意图和实际 source/trash 位置恢复，不误删外部文件或丢失操作记录。

## 保存服务独立只读审查

已对照 `saved_speech.py`：保存在 `on_accept` 内通过同一 db 获取 job lease，复制、编码、完整解码及独立目录发布登记期间不提前释放；终态后生命周期释放来源引用。永久作品的播放路径属于独立目录。删除在写事务内同时核查 owner / media 活跃租约与冻结清单，随后才移动目录；删除确认与恢复还共用独立 `saved-deletion.lock`。未发现租约交接或临时回收越界的新增阻塞项。

其他 6 项全套跳过属于 API 环境缺少 Torch 的训练合同，不属于本媒体专项；不能将其表述为本阶段重新执行了真实训练。
