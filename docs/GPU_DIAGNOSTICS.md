# GPU 驻留、排他与受控诊断

推理请求受理时冻结权重对、参考、文本、精度和参数。可选 `job_id` 把 worker 的实际阶段写入同一任务事件：`request_validation`、`dependency_validation`、`reference_validation`、`model_loading`、`model_ready`、`reference_processing`、`text_frontend`、`synthesizing`、`audio_writing`、`audio_written`。事件包含实际 worker PID 和进程启动时间。

`model_loading` 不声明完整模型已驻留。只有 GPT、SoVITS、BERT、HuBERT、v4 vocoder 的全部参数均已在 CUDA，且设备同步完成，才发出 `model_ready / gpu_resident=true`。参考特征与中文文本前端的 GPU 工作分别记录在相应阶段。模型逻辑启用、持有应用 GPU 租约、实际模型驻留是三个不同状态；worker 退出后由监督器发出 `gpu_released / false`，不根据按钮点击猜测释放。

失败结果包含 `phase`、原始与实际参数、精度、`parameters_changed=false`，CUDA OOM 单列 `code=CUDA_OOM`。模型读取、参考处理、文本前端和语音合成异常保留来源。上游异常会先返回 16 kHz 占位音频；适配器在这一步关闭生成器并保留原异常，避免继续执行上游的模型重新初始化。占位音频不会作为作品发布。OOM 不会自动减 batch、降精度、改采样步数或切换模型。

`fragment_interval` 最低为 0.01 秒，因为上游会把更小值自动改成 0.01；应用在请求检查时明确拒绝。默认仍为 0.3 秒。

## 自有进程模拟外部占用

下述脚本不进入应用的 GPU 租约队列，用于模拟一个受我们控制的外部 CUDA 进程。它只管理自己的分配，记录 PID/启动时间/命令/实际 Torch 分配量；不会终止其他程序。证据与 release 文件必须位于本仓库 `data` 下，必须使用尚不存在的新文件名。

```powershell
.\.venv-worker\Scripts\python.exe scripts\gpu_occupancy_probe.py --mode hold --mib 512 --output data\diagnostics\p08-hold.json --release-file data\diagnostics\p08-hold.release --timeout-seconds 600
```

它实际分配并写满 512 MiB CUDA 张量，在 JSON 状态为 `HOLDING` 后即可查询应用系统资源。需要退出时，在另一个终端创建 release 文件：

```powershell
Set-Content -LiteralPath data\diagnostics\p08-hold.release -Value release
```

也支持 `--stdin`：读取一行或 EOF 后退出。超时会自行释放。`RELEASED` 说明张量和 Torch 缓存已释放；记录的 `process_exit_pending=true` 表示 CUDA 上下文需等进程实际退出，调用方仍须按 PID 与启动时间确认退出。Windows 后台启动应使用隐藏窗口或 `CREATE_NO_WINDOW`。

## 独立真实 OOM 探针

```powershell
.\.venv-worker\Scripts\python.exe scripts\gpu_occupancy_probe.py --mode oom --output data\diagnostics\p08-oom.json
```

此专用子进程只尝试一次分配设备总显存 4 倍的 `uint8` 张量；预期立即收到真实 `torch.cuda.OutOfMemoryError`，记为 `OOM_CONFIRMED` 后退出。不会加载训练/TTS 模型，不尝试较小尺寸，不改真实任务参数。如果分配意外成功，会立即释放且不写入该大张量，记录 `OOM_NOT_TRIGGERED` 并返回失败。

## 验证范围

`tests/test_inference_contract.py` 的 5 项测试属于无 GPU 的合同测试，覆盖参数边界、输入身份、阶段与 OOM 报告、禁止失败占位后重载、受控分配计划。它们不代表真实占用或真实 OOM 已执行。真实 GPU 进程、排他队列、资源变化及 OOM 的实机结果另记在阶段验收证据中。
