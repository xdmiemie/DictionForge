# 排障

## 环境和资产

先运行 `scripts/doctor.ps1 -Assets`。API 与模型 worker 使用独立 Python 环境；不要把上游宽松的 requirements 直接安装进 API 环境。实际测试版本见 `configs/api.requirements.txt` 和 `configs/worker.lock.txt`。

资产准备：`scripts/assets.ps1 -Action prepare`。工具先核对固定源码和补丁，再下载锁内资产；下载中断保留已验证文件。单文件导入使用 `-Action import -Id <资产ID> -Source <本地文件>`。导入前后按 SHA256 核对，原文件只读。

`SIZE_MISMATCH` / `HASH_MISMATCH` 指出具体文件。`UPSTREAM_REVISION_MISMATCH` 表示源码与锁不同；不要覆盖不明改动，先检查 `git -C vendor/gpt-sovits diff`。`INSUFFICIENT_DISK` 表示需要释放 DictionForge 以外无关空间或使用更大的数据目录。

安装依赖时所有新增包应受 `configs/worker.requirements.txt` 约束。只补装一个包也可能升级 NumPy 或 Torch，必须重新运行 CUDA、ASR 和 TTS 探针后更新锁。

## 任务没有进度

后台任务区先刷新状态，再展开「工作进程日志」。未知总量的模型加载和下载不会显示虚构百分比。SSE 断线会重连并读取任务快照，服务重启不会删除已提交事件。

Windows worker 的模型子进程受内核 Job Object 管理；launcher 异常退出时也会回收后代。发现已失联的 worker 会标为 INTERRUPTED。不要把 INTERRUPTED 当作已完成或已安全暂停。

## 本地访问

默认地址 `http://127.0.0.1:7867`。服务只接受回环主机名和同源浏览器请求。与 TimbreForge 使用不同端口、独立数据和状态目录。
