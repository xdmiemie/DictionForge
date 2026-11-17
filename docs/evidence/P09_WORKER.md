# P09 逐句推理 worker 证据

本页记录 worker 的实现合同、CPU 验证及对锁定上游的只读核查。真实 GPU / HTTP / 浏览器验收另由主验收脚本记录；CPU fixture 不冒充模型推理或主观试听。

## 冻结请求与结果

- `text` / `source_text` 保存用户原文，包括前后空白与换行。显式 `sentences` 的 `text` 必须严格等于原文 `[source_start:source_end]`；坐标为 Unicode code point 半开区间，索引从 0 开始，ID 不得重复，区间不得重叠。
- 每句 `seed=(base_seed+index)%2**32`。保留 P07/P08 没有 `sentences` 的请求兼容，自动构成一个原文单元；不裁去该兼容请求的原始空白。
- 一个 worker 只创建一个 TTS 实例，顺序调用上游 `run`，逐句显式传入文本和种子。固定 `batch_size=1`、`parallel_infer=false`、`split_bucket=false`、`return_fragment=false`、`text_split_method=cut0`。
- 每句保存独立原生 48 kHz、单声道 PCM16 WAV；完整解码后必须与上游返回 PCM 逐样本相同才原子发布。全段从已落盘句文件重新解码，核对 SHA-256、大小、连续区间，直接连接后再次完整解码验证。无重采样、无裁剪、无新增句间静音。
- 全段 `start_sample` / `end_sample` 是相对完整音频的半开区间，包含该句上游已有的首尾静音。静音观测阈值固定 `{amplitude:32,unit:'pcm16'}`，用 `abs(int32(pcm))>32` 判断有效信号，避免 int16 最小值取绝对值溢出。全句都不超过阈值时定位到该句失败；不把静音产物标为可用。
- `normalized_texts` 来自该句真实 `TextPreprocessor.preprocess` 返回的 `norm_text`，`actual_text` 按该列表顺序连接。参考文本走另一接口，不混入目标规范文本。此值表示文本前端实际输入语义模型的规范文本，不等于人工听辨或 ASR 转写。
- 成功写入并验证一句才增加 `completed_sentences`。失败结果携带实际 `phase`、`sentence_id`、0-based `sentence_index`、`sentence_text`、完整冻结 `sentence`、`actual_seed`、完成数和已完成句描述；失败不返回伪造的完整音频。

## 锁定上游对照

上游为 `20250422v4` / `6a1ece89928f6e5042a3ae0e092c2f47bdbae596`，使用仓库现有补丁锁，无 P09 vendor 修改。

1. `TTS.py:set_seed` 在每次 `run` 的入口调用。非负显式种子不经过随机替代，原值设置 Python、NumPy、Torch CPU / CUDA RNG；语义 `infer_panel_naive_batched` 最终调用 Torch 随机采样。因此 `actual_seed` 记录本次实际传入且由该锁定实现设置的种子。未声称不同硬件或 CUDA 执行下位级确定性。
2. `text_segmentation_method.py:cut0` 本身不按标点拆分，但 `TextPreprocessor.py:pre_seg_text` 仍处理换行、短段合并、必要标点补全和超过 510 字符的内部拆分。一个应用单元允许对应多个规范文本项和内部音频片段。应用不虚构内部字词时间边界。
3. `TTS.py:to_batch` 在 `split_bucket=false` 时按输入索引顺序组 batch。`batch_size=1` / `parallel_infer=false` 保持内部片段顺序；`audio_postprocess` 顺序展开，并为每个内部片段追加上游静音。worker 使用实际返回的样本，静音长度实测而非根据参数推算。
4. 上游异常可能先 yield 16 kHz 静音占位再尝试重载模型。worker 在该占位处关闭 generator、保留已观测的原始异常阶段，防止把占位当成功或在失败后额外加载一套模型。

## CPU 合同验证

执行：`.venv-api\Scripts\python.exe -m pytest tests/test_inference_contract.py tests/test_sentence_inference_worker.py -q`，`PYTHONPATH=backend`，结果 **19 passed**。没有启动 CUDA 或加载模型权重。

覆盖：原有参数、身份和 OOM 阶段合同；Unicode 非 BMP 码点与前后空白；种子回绕；无效文本区间 / 身份拒绝；同一 model fixture 顺序生成两句；真实 SoundFile PCM16 写入和完整解码；整段逐样本等于两句连接；首尾静音边界、int16 最小值和全静音拒绝；第二句失败保留真实前端阶段及第一句文件；解码不一致不发布文件；不覆盖现有文件或别人留下的临时文件；实际规范目标文本与参考文本分离。

## 真实 GPU 产物独立核查

主任务已串行完成四组 HTTP / RTX 5080 推理，本审计仅以 CPU 读取实际产物，详见 `P09_WORKER.json`。逐句及全段都重新完整解码、校验 SHA-256 / 大小 / PCM16 / 单声道 / 48 kHz，并逐样本核对对应区间。

| 场景 | job ID | 单元数 | 全段样本 / 秒 | 实际生成耗时 / 秒 |
| --- | --- | ---: | ---: | ---: |
| 单句 | `d0dcdf7654654c7b8bb4ed514c62728e` | 1 | 172800 / 3.60 | 9.063 |
| 重复句与换行 | `6b81ed20c2544475813973878e0bd687` | 3 | 399360 / 8.32 | 11.536 |
| 按行分段 | `faac676251e249428b6c3a7f5a5fcefd` | 2 | 384000 / 8.00 | 8.503 |
| 不分句单元含两句正文 | `1411e75b99634c35a7d4ca357adbd264` | 1 | 222720 / 4.64 | 6.618 |

合计 **7 单元、1,178,880 样本、24.56 秒**。四个任务各恰好一个实际 `model_ready` 事件；完成数依次达到 1 / 3 / 2 / 1，事件时序单调。每个任务 Torch CUDA 分配峰值均为 **2,318,460,416 字节**，保留峰值为 2,428,502,016 或 2,430,599,168 字节。

配置为 v4 / CUDA / FP16、sample_steps 32、top_k 5、top_p 1、temperature 1、speed_factor 1、repetition_penalty 1.35、fragment_interval 0.3、基准 seed 42。每个实际 `process.log` 的 `Set seed to` 列表均与各句记录一致，分别为 `[42]`、`[42,43,44]`、`[42,43]`、`[42]`。原文、语言、模型 ID、模型权重哈希及参数均与冻结请求一致。

**上游间隔参数事实：** 本次 `fragment_interval=0.3` 下，各句实测尾静音为 9600–9709 样本，即约 0.2000–0.2023 秒。上游按声学配置采样率生成静音样本，v4 返回音频则为 48 kHz；该参数不能直接宣称为最终 48 kHz 音频的静音秒数。worker 保留实际 PCM，只报告观测边界，不改写上游结果。

规范文本确有变化：中文标点转换成英文标点，短开头“你好”对应的内部规范文本还带上游补入的前导句点；所有真实 `normalized_texts` / `actual_text` 已核查非空并按返回顺序记录。两次重复原句拥有不同源区间、独立 sentence ID、种子 42 / 44 及不同实际音频，没有按文本内容去重误合并。

主任务另在 `P09_ASR.json` 记录 7 句真实离线 CUDA ASR：前 6 句去标点 CER 为 0，最后一单元出现“句 / 据”同音字差异（CER 0.05263）。这只提供内容 / 句序客观核查，不构成主观音色质量判断。本审计未重新占用 GPU、未创建作品库保存或数据回流，也未执行 Git 提交或远端推送。
