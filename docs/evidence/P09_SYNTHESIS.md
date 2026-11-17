# P09 文本与逐句合成实测

日期：2026-11-17。机器、依赖与资产沿用 P02 锁；RTX 5080 16GB，驱动 621.01，Torch 2.9.0+cu128，Python 3.12.10。模型 `a974c5da69695d0aba3477bff5c7b768`，参考 `563ea3259eed48938003d5bb0566d68e`。原文、模型/参考哈希和有效参数均保存在 `P09_HTTP.json` 与各任务请求中。

## 真实 GPU 合成

执行 `scripts/validate_p09.py`，经过真实 HTTP 受理、GPU 排队、模型加载、文本前端、GPT 语义与 v4 声学/声码器生成。四组均 COMPLETED：

| 场景 | 任务 ID | 句数 | 样本/时长 | 实际生成耗时 | 加载耗时 |
| --- | --- | ---: | --- | ---: | ---: |
| 单句 | d0dcdf7654654c7b8bb4ed514c62728e | 1 | 172800 / 3.60 s | 9.063 s | 16.501 s |
| 多句、重复句、首尾空白 | 6b81ed20c2544475813973878e0bd687 | 3 | 399360 / 8.32 s | 11.536 s | 13.550 s |
| 按行，两行内保留标点 | faac676251e249428b6c3a7f5a5fcefd | 2 | 384000 / 8.00 s | 8.503 s | 14.691 s |
| 不分句，两句话一个单元 | 1411e75b99634c35a7d4ca357adbd264 | 1 | 222720 / 4.64 s | 6.618 s | 14.077 s |

四组全为原生 48000 Hz 单声道 PCM16、非静音，Torch 峰值均 2318460416 字节。seed42 起、逐句43/44；sample_steps32、top_k5、top_p1、temperature1、speed_factor1、repetition_penalty1.35、fragment_interval0.3、fp16。没有重采样伪装、静默降参或失败自动重试。

每任务只出现一次真实 `model_ready`，五个实际 CUDA 模块经过设备验证；每句 seed 日志、规范化文本与完成事件另见 `P09_WORKER.md`。各句 WAV 完整解码后与全段对应样本区间逐样本相同，句序连续覆盖整段，无重叠和遗漏。重复的第 1/3 句拥有不同 ID、原文位置和实际种子。源文本范围为 Unicode code point 半开区间，所有 `text[source_start:source_end]` 精确等于该句文本。

合成产物：`E:\DictionForge\data\runtime\tts\<任务 ID>`；进程日志：`E:\DictionForge\data\runs\<任务 ID>\process.log`。任务请求、逐句/全段 WAV、effectives 和结果均保留，媒体在 SQLite 登记。

## 冻结、幂等与错误

每组重复提交同一 request_id 只返回原 Job。完成四组后重放第一组请求，当前结果仍为第四组，不发生指针倒退。七种无效请求（空文本、空白、仅标点、fr 语言、未知断句规则、speed_factor=0、NUL 控制字符）均 HTTP422，并保留同一当前结果。具体错误及冻结快照见 `P09_HTTP.json`。

结果发布前再次检查模型/文本/语言/权重哈希/参数/参考身份、每句稳定 ID 与种子、坐标类型、WAV 完整格式和 PCM 拼接、非静音及静音边界。失败不登记媒体；worker 错误保留具体阶段和句子。CPU 故障注入只用于合同验证，不冒充真实 GPU 故障。

## 真实 ASR 内容复核

GPU 合成结束后，在空闲 GPU 上执行锁定本地 FunASR：

```powershell
& .\.venv-worker\Scripts\python.exe -m dictionforge.asr_worker data/p09-asr-request.json data/p09-asr-result.json
& .\.venv-api\Scripts\python.exe scripts/report_p09_asr.py
```

七个独立 WAV 全部返回非空中文转写。前六句去标点后逐字一致；最后一句「分句」识别为同音的「分据」，1/19 字符编辑距离（5.26%），其余文本顺序完整。全部低于预设 15% 内容复核阈值。完整预期/识别文本、逐句耗时和固定 ASR revisions 见 `P09_ASR.json`；原始请求及结果保存在 data。ASR 是客观内容复核，不代表主观音色、自然度或人工听感。

## 回归与范围

全套 `.venv-api\Scripts\python.exe -m pytest tests -q --basetemp=data/pytest-p09-full`：**189 passed, 6 skipped**，68.09 秒。6 项跳过为 API 环境没有 Torch 的训练循环合同，相关实现未在 P09 更改，P06–P08 独立 worker 验证证据保留。一个 Starlette/httpx 弃用警告，不影响运行；未为消除警告漂移依赖锁。

原有 12 份景元原始 wav/lab 再次核对全部 SHA256 一致（`P09_ORIGINALS.json`）。TimbreForge 只读复核发现外部提交从初始 e2884c7 前进为 309adfe「清理文件」，工作树干净；本项目没有写入、回滚或修改该历史。

真实浏览器结果另见 `P09_BROWSER.md/.json`。P10 的作品保存/MP3/回收和 P11 句子回流尚未实施；P12 主观音质与最终 Windows 交付尚未执行。按用户指令在 P09 完成后停止，不推送。

## 收尾补验与停机

初次四组由真实HTTP创建，浏览器读取播放；另补一次页面有效按钮端到端生成，验证非默认参数实际进入GPU。任务 `70f2d6554c8548afa3d8bab86a1effba`，seed2026/speed1.15/sample_steps16，实际48k/117120样本/2.44秒，生成5.21秒，成为最终current。浏览器5项补验PASS，证据 `P09_BROWSER_SUBMIT.md/.json`。本条未额外进行ASR，不混入前述七句统计。

服务审查补齐推理前、子进程退出后和发布事务内的取消检查，完成产物损坏重验改为FAILED。收尾专项41项通过；未重新执行已通过且未变更的训练GPU场景。取消/发布线性化边界与未实测的后到控制时序明确记录于 `P09_SERVICE.md`，完整取消/保存/清理并发仍待P10。

旧服务在浏览器关闭后的Windows残留HTTP连接上无限等待，曾核对无活动任务/子进程后终止owned PID48232；抢先新启实例因app.lock退出，无并行服务进入。现有serve加入15秒HTTP优雅关闭上限。最终PID47264在安全停止endpoint后正常exit0，无超时或强杀；停机后0活动任务、0服务/worker进程、无7867监听、SQLite integrity_check=ok，当前WAV哈希一致。`P09_HANDOFF_STATE.json` 为实际停机和当前结果证据。P12最终Windows启停脚本仍未实施。
