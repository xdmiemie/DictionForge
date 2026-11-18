# 排障

## 环境和资产

先运行 `scripts/doctor.ps1 -Assets`。API 与模型 worker 使用独立 Python 环境；不要把上游宽松的 requirements 直接安装进 API 环境。实际测试版本见 `configs/api.requirements.txt` 和 `configs/worker.lock.txt`。

资产准备：`scripts/assets.ps1 -Action prepare`。工具先核对固定源码和补丁，再下载锁内资产；下载中断保留已验证文件。单文件导入使用 `-Action import -Id <资产ID> -Source <本地文件>`。导入前后按 SHA256 核对，原文件只读。

`SIZE_MISMATCH` / `HASH_MISMATCH` 指出具体文件。`UPSTREAM_REVISION_MISMATCH` 表示源码与锁不同；不要覆盖不明改动，先检查 `git -C vendor/gpt-sovits diff`。`INSUFFICIENT_DISK` 表示需要释放 DictionForge 以外无关空间或使用更大的数据目录。

安装依赖使用 `configs/worker.lock.txt` 和 `worker.sources.txt`。后者固定 Windows TorchCodec wheel；不要把 CUDA 索引中的 Linux TorchCodec 构建当作本机可用版本。只补装一个包也可能升级 NumPy 或 Torch，必须重新运行 CUDA、ASR 和 TTS 探针后更新锁。

工具下载报错时保留 data/downloads 中的 .part 文件并重试准备入口；未通过大小和 SHA256 检查的包不会安装。工具文件被修改时脚本明确拒绝覆盖，先保留改动再排查。`setup.ps1 -Offline` 需要完整工具包及依赖缓存，不会自动联网补齐。

所有运行时都位于本项目，`sys.base_prefix` 应指向 vendor/tools/python。重建前停止服务，再用 `setup.ps1 -Worker -Recreate`；旧虚拟环境会移到 .venv-*-backup-*，原前端依赖保留在 data/environment-backups。不要把旧备份误认为当前运行环境。

## 任务没有进度

后台任务区先刷新状态，再展开「工作进程日志」。未知总量的模型加载和下载不会显示虚构百分比。SSE 断线会重连并读取任务快照，服务重启不会删除已提交事件。

模型导出和作品保存使用独立 CPU 队列，不需要等待 GPU 训练结束。没有运行任务却长期排队时，查看「本机环境 → 任务调度」：服务可连接不代表后台调度正常。「自动恢复中」表示调度异常后正在重试，原任务保留；「未响应」表示调度线程停止或超过 60 秒没有完成一轮检查。页面会同时显示提示，避免反复提交同一导出。

调度异常记录在 `data/logs/scheduler-errors.jsonl`，包含队列、时间及完整异常栈；相同队列的相同错误最多每 30 秒记录一次。`/api/health` 的 scheduler 字段可查看线程、心跳、当前错误和上次错误；恢复后 last_error 保留用于排查，current_errors 清空。不要手动删除锁文件或修改数据库状态。2026-11-18 曾出现空闲队列无人领取，已通过正常领取机制完成原导出，并补上逐队列异常恢复；旧服务日志为空，原始触发异常未能确认，见 `evidence/EXPORT_QUEUE_RECOVERY.json`。

Windows worker 的模型子进程受内核 Job Object 管理；launcher 异常退出时也会回收后代。发现已失联的 worker 会标为 INTERRUPTED。不要把 INTERRUPTED 当作已完成或已安全暂停。

## 本地访问

默认地址 `http://127.0.0.1:7867`。服务只接受回环主机名和同源浏览器请求。与 TimbreForge 使用不同端口、独立数据和状态目录。

启动提示端口由其他实例占用时，核对 `/api/health` 的 workspace/data_root，不要强行结束该进程。应用脚本只复用匹配的工作区。可为 start/stop 同时指定相同 -Port，或先正常停止原工作区实例。

停止提示存在运行或排队任务时，先在页面暂停训练并等到「已暂停」，或等待任务结束。关闭命令窗口不等于安全暂停；服务本身已从控制台分离。SSE/媒体连接残留时，HTTP关闭最多等待15秒，之后应用仍会正常收尾；这与强杀训练不同。

## 标注、回流与初始化

ASR 可能把角色专名写成同音词。本次真实识别出现“罗父云奇将军警员”，正确标注为“罗浮云骑将军景元”。优先根据原录音和准确 lab 校对；拆片不能复制整句。已发布版本不可原地改写，在页面「修改标注」建立后继草稿。

回流的 ASR 或特征失败会保留独立草稿；校对后用回流面板的专用发布按钮重试。普通数据草稿的发布入口不会跳过回流特征校验。重复请求、相同音频及并发追加不会覆盖父版或增加重复片段。

来源模型 rank 不兼容、初始化权重/依赖哈希变化、预检后配置变化都会拒绝训练；重新选择兼容模型并预检。新数据微调是新运行，优化器计数从零；「继续训练」则要求原冻结数据和配置，恢复完整状态。

## 磁盘和备份

一次完整双阶段检查点约为 S2 1.85GB + GPT 0.93GB，历史恢复点、导出快照和多轮训练会继续占用空间。清理应使用页面的引用保护功能；不要手删正在使用的模型/数据/恢复点。迁移时备份整个 data（尤其state数据库、models、datasets、runs和saved_speech），并保留资产锁与上游源码/补丁。原始录音另行保留。
