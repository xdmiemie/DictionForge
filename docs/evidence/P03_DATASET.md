# P03 真实 HTTP 数据闭环验收

2026-11-17（Asia/Hong_Kong）。服务 `http://127.0.0.1:7867`。通过实际 HTTP API 执行扫描、整理、真实 ASR、来源标注核对、发布、从版本派生新草稿与再次发布。没有直接写 SQLite；仅在核对媒体路径时只读查询登记。

## 可复现入口

```powershell
.venv-api/Scripts/python.exe scripts/validate_p03.py
# 已有父版本时，只检查派生与重新发布（不重复 ASR）：
.venv-api/Scripts/python.exe scripts/validate_p03.py --derive-from eabb1d2c8a494054bfcb4eac28f6c31f
```

脚本以幂等 request_id 提交，默认从 `D:\中文 - Chinese\景元` 只读复制 `archive_jingyuan_1/11/12/13/14/15.wav` 和同名 `.lab` 到 `data/fixtures/景元 训练`。若已存在副本哈希不同会拒绝覆盖。原始 12 个文件的前后 SHA256 和副本 SHA256 全部一致，详见 JSON。

整理设置为 `silence_seconds=3`、`silence_db=-40`、`min_seconds=1`、`max_seconds=12`，6 个源文件均保留为单个完整文本单元，仅裁去头尾低电平区间。扫描 53.1677917 秒，保留 52.72 秒。脚本若发现一个源文件被拆为多个片段，会保留未审核草稿并报错，不把整句标签套到多个片段。

## 实际结果

- 扫描6个有效 WAV，均可定位到中文空格目录；原录音和正式发布片段 Range `bytes=0-255` 均返回 HTTP 206 与256字节。
- 整理后通过 `/api/dataset-drafts/{id}/asr` 对6个片段执行真实 GPU ASR。逐条候选保存在 `data/p03-http/before-review.json` 及初始验收 JSON。
- 根据同名 `.lab` 的明确源文件归属校正 ASR 专有名词和文字差异，确认保留片段。**此处是来源文本和边界核对，没有声称完成主观试听或逐音素人工听审。** 后续角色音质验收仍须单独进行。
- UTF-8 `train.list` 有5条，每行恰好4字段；1个独立真实来源组留出验证，其余5组训练。同组不跨划分。
- 初始版本 `eabb1d2c8a494054bfcb4eac28f6c31f` 已发布并保留。发现正式媒体引用需修正后，重启服务，从该版本新建草稿，修改一条标注为其原值，再发布子版本；没有改写父版清单。
- 最终用于 P04 的版本为 **`76b5a08af4ca471f9c6fa9c903347e69`**，数据集 **`09e42df26bf30b98cea3880e`**，角色景元。6片/52.72秒，5训练/1验证。
- 子版本的 `parent_id` 正确，父 `manifest.json` SHA256 前后相等，片段 ID 与训练/验证分组保持不变。6个新的媒体 ID 均指向子版本正式目录，owner 为子版本，Range 全部206。

正式训练清单：`data/datasets/09e42df26bf30b98cea3880e/76b5a08af4ca471f9c6fa9c903347e69/train.list`。

## 证据

- `P03_DATASET_INITIAL.json`：完整初次扫描、整理、ASR请求ID、标注候选与来源校正、原文件保护及初始发布结果。
- `P03_DATASET.json`：最终派生关系、父版哈希、正式媒体路径与 Range、最终版本ID、源文件前后哈希。
- `data/p03-http/before-review.json`：实际 ASR 草稿快照。
- 原始录音、复制副本及大产物均不进入 Git。
