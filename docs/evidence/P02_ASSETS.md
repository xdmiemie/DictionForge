# P02 资产准备实测

日期：2026-11-17（Asia/Hong_Kong）；Windows 11 / E:\DictionForge。

资产工具：`scripts/assets.py`（Python 标准库）与 `scripts/assets.ps1`。

- `prepare`：源码缺失时克隆固定 tag，检查 commit；已有源码改动明确报错并保留；下载全部锁定资产并映射。
- `download --group v4|asr|text`、`check`、`map`；`import --id <ID> --source <可信本地文件>`。
- 数据根使用 `DICTIONFORGE_DATA`，默认 `E:\DictionForge\data`；相对资产路径始终限制在 assets 内。
- 下载/导入在目标目录写唯一 `.part`，校验字节数和 SHA256、fsync 后 replace；失败保留原文件。目标卷必须保留至少 2 GiB。
- 上游硬编码路径用逐文件硬链接映射；若硬链接不支持则复制。已有不同内容文件拒绝覆盖。G2PW zip 只展开锁内成员，逐成员哈希验证，不使用无约束 extractall。

## 固定资源

| 资源 | 固定 revision | 本地路径 |
| --- | --- | --- |
| 官方 GPT-SoVITS v4 S1/S2/vocoder、CN-HuBERT、中文 RoBERTa | `8f8857563c040d542de108a2c18b0425abd6c9c5` | `data/assets/GPT-SoVITS` |
| funasr/paraformer-zh | `d7811ee3ac581fbcfdeb37c98c6ba674028433dc` | `data/assets/asr/paraformer-zh` |
| funasr/fsmn-vad | `df20e6b30c653645fa4ff125cacfcabd1020a669` | `data/assets/asr/fsmn-vad` |
| funasr/ct-punc | `d0e55e2b8722a78b63705ff443d09c4f86e5d750` | `data/assets/asr/ct-punc` |
| 上游指定 Modelscope kamiorinn/g2pw | `827f4a519b083e3f37c790c938300241e75692d5` | `data/assets/text/G2PWModel_1.1.zip`、展开后的 `G2PWModel` |

HF 权重 SHA256 来自固定 revision 的 LFS oid 并用下载字节重新计算；小文件按固定 revision 下载并计算。G2PW 归档 SHA256 来自 Modelscope 文件 API 并核验下载结果，展开文件哈希另入锁。

实际权重、配置、URL、字节数及 SHA256 见 `configs/assets.lock.json`。主下载资产 4,435,675,098 字节，G2PW 展开约 637 MB；硬链接避免上游副本再次占用同等空间。

## 验证命令

```powershell
python scripts/assets.py download --report data/assets-download-report.json
python scripts/assets.py download --group text --report data/assets-text-report.json
python scripts/assets.py map --group text
python scripts/assets.py check --group v4 --report data/assets-v4-check.json
.venv-api\Scripts\python.exe -m pytest tests/test_assets.py -q --basetemp data/pytest-assets
```

资产故障回归：6 passed（源文件保护、错误 hash 导入不覆盖旧件、映射冲突保护、越界路径、只解锁定归档成员、缺文件错误）。v4 9 个文件全部 PASS；G2PW 归档与 8 个成员 PASS。完整最终检查见相邻 JSON 证据。

资产准备通过不等同于真实 ASR/TTS 通过。CUDA、真实语音转写与原生 48 kHz 推理由独立 P02 环境验收记录报告。未启用语种的文本前端资源不作已通过声明。

## 后续审查修正与补丁

`check` 现在同时校验 data/assets、G2PW 展开成员及所有 vendor 映射路径；映射缺失或损坏不会报告 PASS。完整 24 项再检查已通过。

`prepare` 集成 `scripts/apply_patches.py`：补丁文件 SHA256 与每个被改文件的 before/after SHA256 都锁定；文件内容哈希只规范化 CRLF/LF。仅当源码处于已知补丁边界才继续应用，重复运行幂等，未知本地改动保留并报错。

0001-headless-audio.patch 将 `tools/my_utils.py` 的 Gradio import 移到两个专用 UI 检查函数内；音频解码行为保持一致，独立 worker 不必导入 Gradio。锁与应用器配合，不直接 reset 用户源码。

新增回归命令：`.venv-api\Scripts\python.exe -m pytest tests/test_assets.py tests/test_assets_mapping.py -q --basetemp data/pytest-assets-mapping`，结果 **8 passed**。包括映射缺失/损坏、真实 Git fixture 首次补丁应用/幂等重放，以及未知源码编辑保护。

补丁 0002-reference-soundfile.patch：管理区参考音频为无损 WAV，读取采用 soundfile float32、always_2d 后转 channel-first tensor，绕开 TorchAudio 2.9 在 Windows 上额外 TorchCodec/FFmpeg DLL 绑定。补丁没有更改 v4 输出采样率或声码器。真实 TTS 回归由环境探针报告。

实际 `python scripts/apply_patches.py` 返回固定 commit、patches=2、applied=0；已应用状态 hash 核对通过。`python scripts/assets.py prepare --group text --report data/assets-prepare-patched.json` 重复准备亦 PASS。
