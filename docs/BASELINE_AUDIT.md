# TimbreForge 实现基线审计

审计日期：2026-11-17（Asia/Hong_Kong）。本次仅只读访问 `E:\TimbreForge`；未修改原工程、原录音、虚拟环境、数据或历史。工作区和父级未发现独立 `AGENTS.md`；用户已澄清项目计划即本任务指引。主实施者通过授权只读 Git 核对 TimbreForge HEAD 为 `e2884c72604766ce6d4193ebdba56f6b4c040785`，现有改动为 `D DictionForge_Codex_Project_Plan.md`，必须保留。

## 核对来源

- `docs/USER_GUIDE.md`：当前用户操作及回流行为。
- `frontend/src/main.tsx`、`style.css`：中文双标签、目录选择、扫描播放、训练、模型、后台任务、作品库。
- `backend/timbreforge/routes.py`、`api.py`：真实 API、请求校验、幂等提交、SSE。
- `datasets.py`、`features.py`、`training_config.py`、`rift_training.py`：数据分组、特征缓存、真实训练与恢复。
- `augmentation.py`：从来源模型找到原训练数据，追加新版本，保留验证集，记录 `initial_model`。
- `jobs.py`、`supervisor.py`、`engine.py`、`worker.py`：SQLite 持久任务、进程身份、独占锁及 worker 调用。
- `audio.py`、`covers.py`、`library.py`、`models.py`：原子写、受管理路径、永久保存、引用保护与模型发布。
- `docs/ACCEPTANCE_REPORT.md`、`tests/`：既有测试与验收记录；记录中的 RIFT 实测不计作 DictionForge 的验收。

## 必须保留的操作

| 基线操作 | DictionForge 迁移要求 |
| --- | --- |
| 浏览服务电脑、粘贴路径、角色、语言、递归扫描 | 支持 Windows 原生目录窗口及中文空格路径；目录扫描只读 |
| 逐文件试听、排除和时长统计 | 保留扫描后的可用状态、排除原因和勾选；增加 ASR/校对草稿 |
| 整理→选版本→准备特征→预检→开始训练 | 插入逐段标注审核，特征替换为 GPT-SoVITS 三段上游流程 |
| 累计步数或轮次、物理 batch、高级设置 | S2 与 S1 独立配置；有效参数必须来源于实际 worker |
| 暂停并保存、继续、导出并继续、中止并导出 | 扩展到双阶段与完整恢复点，保存成功后才能显示已暂停 |
| 历史恢复点派生、失败导出重试 | 保留源历史，重试使用同一快照和版本 ID |
| 模型和数据管理 | 展示版本和引用；必要依赖、活跃任务、恢复点受保护 |
| 模型选择、外部导入、开启状态、效果测试 | 外部权重改为 GPT/SoVITS v4 权重对；效果测试用固定文本和参考 |
| 当前结果试听、WAV/320 kbps MP3 保存 | TTS 原生 48 kHz；当前结果和永久作品独立生命周期 |
| 作品筛选、十条分页、本页/跨页批量删除 | 筛选维度为模型、标题、文本，删除确认冻结 ID 集合 |
| 当前结果追加训练数据、跳转新版本 | 从选定句子追加；整句复用文本，拆片逐段标注；默认来源模型初始化，可选官方底模 |

## 适合迁移的实现模式

- `PathField`（前端）和 `folder_picker.py`：原生目录选择、路径输入与错误回退；迁移时使用 DictionForge 的 API 和产品名称。
- `TaskProgress`、后台任务区：真实指标和状态标签；需增加活动阶段及 S1/S2 累计量。
- `SavedLibrary`：十条分页、筛选时清空选择、本页全选和筛选全选；服务端仍须验证删除集合。
- `Jobs.submit()`：事务中的请求键唯一约束，相同载荷返回原任务、不同载荷冲突；独立数据库名必须改为 DictionForge。
- `Supervisor.live_identity()`：PID 与进程创建时间共同识别；独占 worker 锁模式可借鉴，但 DictionForge 需要 CPU 并行和 GPU 专用租约。
- `audio.atomic_json()`、`managed_child()`：写入后 fsync 再原子替换、解析路径边界与 Windows 重解析点拒绝。
- `covers.save_cover()`、`library.discard()`：永久作品独立文件、事务配合移入回收区、Windows 句柄占用后重试清理。
- `augmentation.append_range()`：复制父版、追加 `split=train`、保留验证、来源/哈希/谱系、临时目录通过后原子发布；TTS 必须重新实现句子索引和文本来源。
- `routes.initial_model()`：默认来自回流版本的来源模型；删除或哈希变化时阻止并提示用户选择底模。

## 不可直接照搬的部分与风险

1. 原系统是 RIFT-SVC 声音转换；`audio.RATE=44100`、16 条 batch 预检、RIFT 四类特征、1024-16 架构、说话人映射和单个 `.ckpt` 均不适用于 GPT-SoVITS。
2. 网易云、DPAPI、分离器、歌曲下载和双轨混音不属于本项目。不得引入原项目认证或在 worker 复制无关凭据。
3. 原追加服务使用任意时间裁剪且无文本；TTS 必须按冻结 `sentence_id` 的样本区间追加，拆片后不能给所有片段复用整句文本。
4. 原删除实现主要保护 `QUEUED/RUNNING`；本计划还要求有效恢复点、默认参考和派生版本依赖保护，不能直接复制其保护范围。
5. 原事件结构和训练状态较少；需加入 schema/version、单调序号、控制受理/执行/落盘区分及 SSE 断线重放。
6. 原 supervisor 将所有工作串行化；需将 CPU 与 GPU 调度拆开，并对 worker 子进程树回收及显存释放提供实测。
7. 原验收报告的训练数据为本机生成短合成语音，角色音质待真实角色录音；不能将该报告或相邻模型结果称为新项目已通过。

## 既有验证参考

有价值的回归模式来自 `test_jobs.py`、`test_supervisor_recovery.py`、`test_datasets.py`、`test_checkpoint_index.py`、`test_cover_lifecycle.py` 和 `test_library_upgrade.py`。适合复用其故障场景设计，不应把 RIFT 架构、44.1 kHz 或原数据库表结构直接作为新项目断言。
