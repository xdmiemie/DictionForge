# P02 环境与真实模型验收

日期：2026-11-17（Asia/Hong_Kong）。独立环境 `E:\DictionForge\.venv-worker`，未修改 TimbreForge、astrbot-chat 或用户原录音。测试使用已有明确标注的本地录音只读副本。

## 实际组合

Windows 11 build 26200；Python 3.12.10；RTX 5080；驱动 621.01；Torch/Torchaudio 2.9.0+cu128；NumPy 1.26.4；Transformers 4.48.3；FunASR 1.0.27；Lightning 2.5.5；PEFT 0.14.0；Librosa 0.10.2；FFmpeg 8.1.2 full_build。

`configs/worker.requirements.txt` 是人工维护的直接约束；`configs/worker.lock.txt` 冻结实际 157 个分发包。`uv pip check --python .venv-worker/Scripts/python.exe` 输出 `All installed packages are compatible`。JSON 的 `imports` 字段只是安装元数据；实际 CUDA、FunASR、PEFT、Lightning 和 TTS 模块均已另行导入及运行。完整空环境重建由 P12 另验。

安装使用本机现有 Python 3.12 和只读 RIFT wheelhouse 加速，所有安装目标、临时目录与 uv 缓存在 DictionForge 内。可复现入口：

```powershell
scripts/setup.ps1 -Python <Python3.12路径> -Worker -Uv <uv.exe路径> -Wheelhouse <可选wheel目录>
scripts/assets.ps1 -Action download
scripts/doctor.ps1 -Assets
.venv-worker/Scripts/python.exe scripts/probe.py --output data/diagnostics/cuda.json --audio 'data/fixtures/知更鸟 原始.wav'
.venv-worker/Scripts/python.exe scripts/probe.py --output data/diagnostics/official-asr.json --asr 'data/fixtures/知更鸟 原始.wav'
.venv-worker/Scripts/python.exe scripts/probe.py --output data/diagnostics/official-tts.json --tts --reference 'data/fixtures/景元 参考.wav' --reference-text (Get-Content -LiteralPath 'data/fixtures/景元 参考.lab' -Raw)
```

## 结果

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| CUDA 张量 | PASS，真实 CUDA 64×64 矩阵乘法和结果比较；Torch 峰值 8,581,120 B | `P02_CUDA.json` |
| 中文空格路径 | PASS，FFmpeg 完整解码且源副本 SHA256 相等 | `P02_CUDA.json` |
| 中文 ASR | PASS，Paraformer + FSMN VAD + CT Punc 在 CUDA 上识别 10.872 秒真实录音；识别 18.187 秒，Torch 峰值 2,069,037,056 B | `P02_ASR.json` |
| 官方 v4 | PASS，官方 GPT/S2/声码器/BERT/HuBERT 全部真实加载；原生 48,000 Hz，170,880 样本（3.56 秒）非静音 WAV；合成 68.219 秒，全进程 78.984 秒；Torch 峰值 2,329,732,608 B | `P02_TTS.json` |
| 输出回识别 | PASS，真实 FunASR 能解码并识别生成短句，项目名产生同音字误差 | `P02_TTS_ROUNDTRIP_ASR.json` |
| 进程释放 | TTS 进程正常退出后 nvidia-smi 回到约 1149 MiB 桌面占用，无模型进程驻留 | 本次执行记录 |
| PowerShell 语法 | setup.ps1、doctor.ps1 通过 PowerShell Parser，无语法错误 | 本次执行记录 |

生成文件 `data/diagnostics/official-tts.wav`，SHA256 `0e34ab5f7d6a0810d2218f68f9292db76ad39c9f22aea8e6b3c3e00063f8ab55`。参数：中文、FP16、物理推理 batch 1、语速 1.0、seed 42、sample_steps 32、top_k 5、top_p 1.0、temperature 1.0、repetition_penalty 1.35、parallel_infer false。正文为“你好，欢迎来到声句工坊。”；参考为景元 8.931905 秒原始录音和同名准确 `.lab`。实际有效 YAML 保存于 `data/diagnostics/official-tts.effective.yaml`，不会写入 vendor 跟踪配置。

ASR 原始候选包含“不必纠结”等识别错误，准确标注为“不必拘谨”；回识别把“声句工坊”写作“生聚工坊”。这些是真实识别输出，不能替代人工校对或主观角色音质试听。

## 排障和兼容补丁

1. 初次沙箱安装 Torch 时 uv trampoline 的 PE resources 写入被拒；使用已授权执行并将 TEMP/TMP 指向 `data/tmp` 后离线重装成功。
2. 上游间接导入 matplotlib、OpenCC、ONNXRuntime，补齐并固定。单独装 matplotlib 曾把 NumPy 升到 2.5.5，引发 Numba 拒绝；恢复 1.26.4，并固定 contourpy 1.3.3、后续安装均带约束。最终 `pip check` 全通过。
3. `0001-headless-audio.patch` 将工具内 Gradio 导入延迟到仅 UI 使用的检查函数，使 TTS 音频解码不依赖 Gradio。
4. TorchCodec 0.8.1 无法使用本机 Windows FFmpeg 8 静态构建。`0002-reference-soundfile.patch` 以 SoundFile float32/channel-first 读取受管理无损参考，保持源采样率；不重采样生成输出，不依赖相邻项目 DLL。原始错误保留在 `P02_TORCHCODEC_FAILURE.json`。
5. 补丁由 `scripts/apply_patches.py` 校验 before/after 和补丁 SHA256，可重放。官方模型本次没有发生 weights-only 加载错误，无需放宽 Torch 全局加载策略。

## 原始录音保护

知更鸟来源 `D:\中文 - Chinese\知更鸟\archive_robin_1.wav` 与 `data/fixtures/知更鸟 原始.wav` SHA256 均为 `4cd4e3f25c7e5a8d237761d6d6cd85f994b0706bcf7b4ff22f62b365ef89b28d`。景元来源 `D:\中文 - Chinese\景元\archive_jingyuan_1.wav` 与 `data/fixtures/景元 参考.wav` 均为 `e3db07886320844abb3f4f7537ba21dc61d2ad43ee4c4504378383ace2a8f1ef`。音频、模型与运行日志留在忽略目录，Git 仅记录代码、锁和小型 JSON 证据。
