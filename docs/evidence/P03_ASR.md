# P03 独立 ASR worker 验收

日期：2026-11-17（Asia/Hong_Kong）。使用 P02 已通过的 Python 3.12.10 / Torch 2.9.0+cu128 / FunASR 1.0.27，RTX 5080。

## 合同

```text
python -m dictionforge.asr_worker request.json result.json
```

`request.json` 接收绝对 `upstream` 和 `clips:[{id,path,language:"zh"}]`。结果按输入顺序保留每条 ID，返回 `text`、`language`、`error`、`confidence:null`、`seconds`。错误为 `{code,message}`，成功为 null。一次加载 Paraformer + FSMN VAD + CT Punc，按片段依次调用；同请求后续片段复用模型。第一条的 seconds 包含模型加载和资产校验，后续仅为该段处理耗时。

在导入 FunASR 前，核对资产锁中 ASR 文件大小和 SHA256；绑定本地绝对模型目录，设置离线环境并拦截 socket 连接/DNS，避免库隐式下载。模型资产缺失或损坏逐条报错；不静默切换 CPU。当前经过实测的识别语言只有 zh，其它语言返回 `UNSUPPORTED_LANGUAGE`。输出使用原子 JSON 写入。非法顶层请求退出码 1；逐条失败仍保存其余条目并退出 0，调用方须检查 error。

已校对条目保护和失败重试由上层数据服务控制，本 worker 只识别请求显式传入的条目。

## 自动合同测试

```powershell
$env:PYTHONPATH='E:\DictionForge\backend'
.venv-api/Scripts/python.exe -m pytest tests/test_asr_contract.py -q --basetemp data/pytest-asr-contract
```

结果：**5 passed in 0.06s**。覆盖一次模型加载、单条异常不影响前后成功片段、无置信度输出、不支持语言/缺文件不加载模型、缺资产逐条报告、网络回退被明确阻止、socket 恢复、重复 ID 拒绝和原子错误清单。模型调用边界测试使用可控桩；下节为真实 GPU 证据。首次未指定 basetemp 时测试被沙箱临时目录权限阻止，改为工作区内临时目录后通过。

## 真实 GPU 两录音

输入 `data/p03-asr/request.json`，两个片段分别指向 `data/fixtures/知更鸟 原始.wav`（10.872 秒）和 `data/fixtures/景元 参考.wav`（8.931905 秒）。二者源文件及准确 `.lab` 的来源与保护哈希见 P02_ENVIRONMENT.md。

```powershell
$env:PYTHONPATH='E:\DictionForge\backend'
$env:PYTHONUTF8='1'
.venv-worker/Scripts/python.exe -m dictionforge.asr_worker data/p03-asr/request.json data/p03-asr/result.json
```

退出码 0，两条 error 均为 null，confidence 均为 null：

| ID | 实际 ASR 候选 | seconds |
| --- | --- | ---: |
| robin | 晚上好，我是来自家族的歌者，知更鸟。二、不必纠结。这只是一次舞台下的私人问候，能为我抽些时间吗？ | 16.687 |
| jingyuan | 我是罗父云奇将军警员啊，不必拘谨将军只是一时的身份，你称呼我警员便可。 | 0.235 |

错误专有名词和“拘谨”识别偏差真实保留，应在校对页依据 `.lab` 和音频修正；不把 ASR 成功等同于标注正确。

结果记录资产锁 SHA256 `dd66b73f1cb31a65ccd5736b64fe715d662a7d62884663f1d4b5d90bbbd93c07`，以及三个固定仓库 revision：

- Paraformer：`d7811ee3ac581fbcfdeb37c98c6ba674028433dc`
- FSMN VAD：`df20e6b30c653645fa4ff125cacfcabd1020a669`
- CT Punc：`d0e55e2b8722a78b63705ff443d09c4f86e5d750`

完整请求、原子结果和执行日志位于 `data/p03-asr/`。实际模型只加载一次，进程退出即释放 GPU；未写入原录音、相邻环境或上游源码。
