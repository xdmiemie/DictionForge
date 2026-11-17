# P03 前端与实际契约核对

日期：2026-11-17，Windows；后端 `http://127.0.0.1:7867`。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 生产构建 | PASS | `pnpm run build`：TypeScript 检查通过，Vite 6.3.6 构建 30 modules |
| 目录/扫描/整理契约 | PASS（源码核对） | 逐项对照实际 `routes.py`：`/folders/pick`、`/datasets/scan`、`/datasets/prepare` 字段一致；切分输入范围与 Pydantic 校验一致 |
| 扫描状态 | PASS（源码核对） | 兼容 `ready/silent/duplicate/damaged/short`；展示排除原因、`scan.seconds` 可用时长和音频 ID；仅剩无效素材时禁用整理 |
| 草稿错误/审核 | PASS（源码核对） | `asr_error` 支持字符串、`{code,message}` 和 null；草稿全局 ASR 错误及逐段错误均能显示；编辑文本取消该段确认标记 |
| 发布及版本形状 | PASS（源码核对） | 保存/发布带 revision；读取发布后 `published` 标志并冻结编辑；版本 `clips` 数组显示 length，不渲染对象 |
| 已发布版本修改入口 | PASS（构建/契约核对） | 版本列表「修改标注」提交 `/dataset-versions/{id}/draft` 和 request_id，打开返回新草稿；当前未保存修改先提示，原版本保留。实际浏览器操作仍待补验 |
| 实际浏览器扫描/校对/试听 | BLOCKED | CUA `getState()` 返回 `browsers=[]`，内置浏览器 provider 未恢复。沿用 P02 原生访问失败记录，不再次无限尝试，也未重复启动 GPU ASR |

P03 实际录音准备、ASR 和音频验收由阶段主验收任务执行。本文件不将源码核对和成功构建视为浏览器业务操作通过。待浏览器可用后补跑：使用 `E:\DictionForge\data\fixtures` 扫描，试听/排除，选择已有草稿并校对发布。
