# GPT-SoVITS v4 上游审计

审计日期：2026-11-17（Asia/Hong_Kong）。源码已实际读取，尚不代表 GPU 训练或推理通过。

## 固定基线

- 官方仓库：https://github.com/RVC-Boss/GPT-SoVITS.git
- 标签：`20250422v4`
- 实际 checkout / ls-remote commit：`6a1ece89928f6e5042a3ae0e092c2f47bdbae596`
- 本地目录：`vendor/gpt-sovits`（忽略源码副本；通过锁重建）
- 获取命令：`git clone --depth 1 --branch 20250422v4 https://github.com/RVC-Boss/GPT-SoVITS.git vendor/gpt-sovits`
- 源码及许可证由官方仓库提供。应用补丁需独立归档，不能假设原生脚本支持本计划的全部控制协议。

## 三段特征与训练视图

| 入口 | 实际协议 | 应用必须补齐 |
| --- | --- | --- |
| `GPT_SoVITS/prepare_datasets/1-get-text.py` | 环境变量 `inp_text, inp_wav_dir, exp_name, i_part, all_parts, opt_dir, bert_pretrained_dir, is_half, version`；`.list` 每行恰好 4 个 `|` 分隔字段；按 basename 键；输出 `2-name2text-0.txt`、中文 `3-bert/<name>.pt` | 唯一 basename、字段规范化、文本缓存失效、聚合为 `2-name2text.txt`、逐条检查音素/BERT 维度；脚本捕获单条异常仍可退出 0 |
| `2-get-hubert-wav32k.py` | `cnhubert_base_dir`；输出 `4-cnhubert/<name>.pt` 和 `5-wav32k/<name>`；32 kHz PCM，HuBERT 输入 16 kHz；现存 SSL 文件直接跳过 | 必须同时核对 WAV 和 SSL，缺失其中任一应重算；静音不能传入除以峰值路径；记录半精度 NaN 后自动 float 回退 |
| `3-get-semantic.py` | `pretrained_s2G, s2config_path`；读取 SSL，输出 `6-name2semantic-0.tsv`，无表头；按权重尺寸选择 v1/v2/v3 提取器，v4 底模共用 v3 语义网络 | 合并 `item_name\tsemantic_audio` 表头，核对集合、token 维度、资产与配置指纹；不能因退出 0 就报告成功 |
| S1 DataModule | `AR/data/data_module.py` 使用文本、语义索引；`if_dpo` 会减半 batch；再次按样本数自动缩小 batch；`persistent_workers=True` | 预检实例化真实加载器并展示实际 batch，明确禁用未接通 DPO；num_workers=0 需要适配；留出音频不能进训练视图 |
| S2 v4 DataLoader | `module/data_utils.py` 的 `TextAudioSpeakerLoaderV4`、`TextAudioSpeakerCollateV4`，`DistributedBucketSampler` 边界 32..1000；原脚本 worker=6 | 真实读取并检查过滤后样本/批次；小数据、重复扩展与分桶过滤要明确报告 |

## 双阶段训练

S2 官方入口 `GPT_SoVITS/s2_train_v3_lora.py --config <json>`：必须设置 `model.version=v4`；使用 `SynthesizerTrnV3` 的 v4 分支和 `TextAudioSpeakerLoaderV4`。LoRA 作用于 CFM 的 `to_k,to_q,to_v,to_out.0`，alpha=rank。底模读取 `train.pretrained_s2G` 的 `weight`；然后创建 PEFT 适配层。来源 LoRA 不能直接当底模传入，也不能先 merge 再重复加载；新微调必须底模→创建一次适配器→装入来源 LoRA→新优化器。

S1 官方入口 `GPT_SoVITS/s1_train.py --config_file <yaml>`，模板为 `GPT_SoVITS/configs/s1longer-v2.yaml`；环境 `hz=25hz`。`pretrained_s1` 由 Lightning 模型读入；输出目录、文本索引、语义表必须冻结到运行。

官方 WebUI 映射见 `webui.py:584-725`：v4 GPT 为 `GPT_SoVITS/pretrained_models/s1v3.ckpt`，S2 为 `GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth`。WebUI 非 half 分支会自动把 batch 减半；DictionForge 不可复制这种静默调整。

## 恢复、进度和导出差异

- S2 原生 `utils.save_checkpoint` 只存 `model, iteration, optimizer, learning_rate`。没有 scheduler/scaler/RNG/sampler 游标/数据指纹；只能按 epoch 推算 global_step。
- S2 恢复用宽泛 `except`，损坏恢复点会静默回到官方底模；`utils.load_checkpoint` 对缺键和 shape 不匹配用当前参数代替。应用必须严格验证并显式失败。
- S2 scaler 可能跳过更新，但原生 global_step 仍每 batch 自增。真实优化器步事件必须由实际 optimizer 更新触发。
- S2 scheduler 在恢复时重放 step，并不加载完整状态。原生只在 epoch 保存，不能据此承诺轮中精确恢复。
- S1 Lightning 完整 ckpt 与 `weight/config/info` 的推理导出不同；完整 ckpt 包括优化器及训练循环状态，但自定义 sampler/RNG/已消费游标需另行验证。原生查目录最新点，没有应用显式恢复点指纹。
- S1 `limit_val_batches=0`，DataModule 的 dev 等于 train，不能展示独立验证 loss。
- S2 推理导出仅选可训练参数，含 LoRA rank 和版本元数据，不能当完整恢复点。`process_ckpt.py` 的 v4 文件头与 `load_sovits_new` 协议必须保留。
- 暂停保存、导出并继续、提前终止导出、阶段交接事务和原子 last 索引均属应用适配项，原生 WebUI 的终止是直接杀进程。

## 推理与参考

`GPT_SoVITS/TTS_infer_pack/TTS.py` 提供 `TTS_Config`、`TTS.run(inputs)`。主参考解码到 16 kHz 后要求 48000..160000 样本，即 3–10 秒（约第 750 行）。模型启用须校验可解码、非空文本、语言、哈希与长度。

v4 声码器读取固定相对路径 `GPT_SoVITS/pretrained_models/gsv-v4-pretrained/vocoder.pth`，配置采样率为 **48000**（约第 589–611 行）。48 kHz 要从真实 `run()` 返回值验证，禁止把其他采样率重采样后标记原生 v4。

LoRA 推理加载在 `init_vits_weights`：识别 v4/LoRA 文件→读匹配底模→创建 PEFT→加载适配参数→`merge_and_unload()` 一次。底模依赖存在性与 rank 必须入模型清单。

`run` 支持 text/text_lang、ref_audio_path、prompt_text/prompt_lang、top_k/top_p/temperature、text_split_method、batch_size、speed_factor、seed、fragment_interval、sample_steps、repetition_penalty 等。内部切分/批排序不等于应用句子索引；应用宜逐句独立调用（batch=1），按返回 PCM 累计样本区间，保留重复句子的独立 ID。

## 资产和 Windows 兼容风险

必须锁定 GPT、v4 S2、vocoder、CN-HuBERT、中文 RoBERTa、FunASR 及对应语言前端资产。上游 requirements 大量使用范围或不锁版本，不能宣称它是经过验证的 Windows 5080 依赖锁。Torch 新版 `weights_only` 默认值变化须检查可信官方 checkpoint 加载；只允许本机明确导入的受信任 pickle 权重。

原生 `my_save` 在当前工作目录写时间戳临时文件后 `shutil.move`，不是本计划要求的同卷原子落盘协议；应用恢复点必须自行在目标目录写临时文件并验证后 `os.replace`。

子进程必须以参数列表调用并设置 cwd 到固定源码根，设置 PYTHONPATH 包含根与 GPT_SoVITS；禁止字符串拼接 shell 命令。资产只读映射到上游预期目录，用户原始录音只读，输出指向 DictionForge 数据根。

## 后续验收入口

P02：真实 CUDA 张量、官方参考推理和 ASR；P04：三段特征、两个真实 DataLoader；P05：参数前后差异和真实更新；P06：S1/S2 暂停重启、游标/优化器/RNG 校验；P07–P09：真实权重对加载和原生 48 kHz；P11：来源 LoRA 只应用一次。上述未运行前均不得标为 PASS。
