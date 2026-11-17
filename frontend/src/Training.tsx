import {useEffect, useMemo, useState} from 'react';
import {active, api, errorText, finiteNumber, Job, JobError, optimizerUpdates, peakBytes, Progress, requestId, stateLabel} from './api';
import type {DatasetVersion} from './Datasets';

type StageConfig = {batch_size: number; precision: string; stop_mode: 'steps' | 'epochs'; target: number; learning_rate: number; rank?: number; accumulate?: number};
type StageResult = {samples?: number; unique_samples?: number; batches?: number; batch_size?: number; precision?: string; [key: string]: unknown};
type Preflight = {config: Record<string, unknown>; config_fingerprint: string; stages: {s1?: StageResult; s2?: StageResult}; ready: boolean; blockers?: (string | JobError)[]; gpu?: {device?: string; free_bytes?: number; total_bytes?: number; peak_allocated_bytes?: number; probe?: string; training_memory?: string}};
type Submit = (path: string, payload: object) => Promise<Job>;
const initialS2: StageConfig = {batch_size: 1, precision: 'fp16', rank: 32, stop_mode: 'epochs', target: 1, learning_rate: .0001};
const initialS1: StageConfig = {batch_size: 1, precision: 'fp16', stop_mode: 'epochs', target: 1, learning_rate: .002, accumulate: 4};

function StageFields({name, value, enabled, onEnabled, onChange}: {name: 's1' | 's2'; value: StageConfig; enabled: boolean; onEnabled: (enabled: boolean) => void; onChange: (value: StageConfig) => void}) {
  const change = <K extends keyof StageConfig>(key: K, input: StageConfig[K]) => onChange({...value, [key]: input});
  return <div className="stage-config"><div className="section-top"><div><p className="eyebrow">{name === 's2' ? 'STAGE 01 / S2' : 'STAGE 02 / S1'}</p><h3>{name === 's2' ? 'SoVITS v4 LoRA' : 'GPT 微调'}</h3></div><label className="check"><input type="checkbox" checked={enabled} onChange={e => onEnabled(e.target.checked)}/>训练此阶段</label></div>
    <fieldset disabled={!enabled}><div className="grid"><label>物理 batch<input type="number" min="1" max="64" step="1" value={value.batch_size} onChange={e => change('batch_size', e.target.valueAsNumber)}/></label><label>训练精度<select value={value.precision} onChange={e => change('precision', e.target.value)}><option value="fp16">FP16</option></select></label><label>停止条件<select value={value.stop_mode} onChange={e => change('stop_mode', e.target.value as StageConfig['stop_mode'])}><option value="epochs">累计轮次</option><option value="steps">累计优化器步数</option></select></label><label>{value.stop_mode === 'epochs' ? '目标累计轮次' : '目标累计优化器步数'}<input type="number" min="1" step="1" value={value.target} onChange={e => change('target', e.target.valueAsNumber)}/></label></div><details><summary>阶段高级参数</summary><div className="grid"><label>学习率<input type="number" min="0.00000001" max="0.1" step="0.00001" value={value.learning_rate} onChange={e => change('learning_rate', e.target.valueAsNumber)}/></label>{name === 's2' ? <label>LoRA rank<select value={value.rank} onChange={e => change('rank', Number(e.target.value))}>{[8, 16, 32, 64, 128].map(rank => <option key={rank} value={rank}>{rank}</option>)}</select></label> : <label>梯度累积<input type="number" min="1" max="64" step="1" value={value.accumulate} onChange={e => change('accumulate', e.target.valueAsNumber)}/></label>}</div></details></fieldset>
  </div>;
}

function PreflightStage({name, result}: {name: 's1' | 's2'; result?: StageResult}) {
  return <article className="preflight-stage"><h3>{name === 's2' ? 'SoVITS / S2' : 'GPT / S1'}</h3>{result ? <><dl><dt>加载器有效样本</dt><dd>{result.samples ?? '未返回'}</dd><dt>独立样本</dt><dd>{result.unique_samples ?? '未返回'}</dd><dt>每轮批次</dt><dd>{result.batches ?? '未返回'}</dd><dt>实际 batch</dt><dd>{result.batch_size ?? '未返回'}</dd><dt>生效精度</dt><dd>{result.precision ?? '未返回'}</dd>{typeof result.removed_native_repeats === 'number' && <><dt>已去除上游重复样本</dt><dd>{result.removed_native_repeats}</dd></>}</dl><details><summary>实际加载器配置与过滤信息</summary><pre>{JSON.stringify(result, null, 2)}</pre></details></> : <p className="muted small">未训练此阶段或没有返回加载器结果。</p>}</article>;
}

function PreflightGpu({gpu}: {gpu?: Preflight['gpu']}) {
  if (!gpu) return null;
  const loaderOnly = gpu.probe === 'loader_tensors_only';
  return <div className="notice preflight-gpu"><strong>{gpu.device ?? 'GPU 资源检查'}</strong>
    {finiteNumber(gpu.free_bytes) && <p>采样时可用显存 {(gpu.free_bytes / 2 ** 30).toFixed(2)} GiB{finiteNumber(gpu.total_bytes) ? ` / ${(gpu.total_bytes / 2 ** 30).toFixed(2)} GiB` : ''}</p>}
    {loaderOnly && finiteNumber(gpu.peak_allocated_bytes) && <p>数据张量搬运峰值 {(gpu.peak_allocated_bytes / 2 ** 20).toFixed(2)} MiB，仅覆盖加载器样本。</p>}
    {(loaderOnly || gpu.training_memory === 'NOT_MEASURED') && <p className="memory-limit"><strong>完整训练显存尚未实测</strong><br/>本次探针没有加载完整训练模型、优化器，也没有执行前向和反向计算。以上数值不能用于估算训练显存；实际峰值由真实训练记录。</p>}
  </div>;
}

type RunStage = Progress & {state?: string; error?: string | JobError};
type Checkpoint = {id: string; path?: string; sha256?: string; stage: string; metrics?: Progress; kind?: string; valid: boolean; reason?: string; protected?: boolean | string[]};
type CheckpointList = {checkpoints: Checkpoint[]; last_valid?: string | Checkpoint | null; invalid?: unknown[]; inherited_start_point?: {id?: string; stage?: string; valid: boolean; reason?: string; selection?: {source_run?: string; parent_seed_reason?: string}} | null};
type ExportJob = Job & {snapshot?: {id?: string; reason?: string; stage_progress?: Record<string, unknown>; [key: string]: unknown}};
type ExportIntent = {id?: string; intent_id?: string; state: string; error?: string | JobError; reason?: string; checkpoint_id?: string; job_id?: string};
type RunView = Job & {run?: {config?: {s1?: StageConfig; s2?: StageConfig}; [key: string]: unknown}; stage_state?: {active_stage?: string | null; stages?: {s1?: RunStage; s2?: RunStage}; [key: string]: unknown} | null; pending_controls?: {action: string; request?: string}[]; exports?: ExportJob[]; export_intents?: ExportIntent[]};
const exportReason = (reason?: string) => ({export: '手动导出', stop_export: '中止并导出', completed: '完成自动导出', import: '外部权重导入'}[reason ?? ''] ?? reason ?? '权重快照');

export function RunStageCard({name, stage, current}: {name: 's1' | 's2'; stage?: RunStage; current: boolean}) {
  const unit = stage?.unit;
  const epochMode = unit === 'epochs' || unit === '轮';
  const steps = stage && optimizerUpdates(stage);
  const peak = stage && peakBytes(stage);
  const value = epochMode ? stage?.completed_epochs : steps ?? stage?.completed;
  const target = finiteNumber(stage?.target) ? stage.target : stage?.total;
  return <article className={'preflight-stage run-stage ' + (current ? 'active-stage' : '')}>
    <div className="section-top"><h3>{name === 's2' ? 'SoVITS v4 / S2' : 'GPT / S1'}</h3><span className="pill">{stage?.state ? stateLabel(stage.state) : current ? '活动阶段' : '等待阶段快照'}</span></div>
    {stage?.error && <p className="inline-error">{errorText(stage.error)}</p>}
    {stage ? <><dl>{finiteNumber(stage.completed_epochs) && <><dt>已完成轮次</dt><dd>{stage.completed_epochs}</dd></>}{finiteNumber(steps) && <><dt>实际优化器更新</dt><dd>{steps}</dd></>}{finiteNumber(stage.processed_batches) && <><dt>已处理 batch</dt><dd>{stage.processed_batches}</dd></>}{finiteNumber(stage.physical_batch_size) && <><dt>物理 batch</dt><dd>{stage.physical_batch_size}</dd></>}{finiteNumber(stage.skipped_updates) && <><dt>跳过的更新</dt><dd>{stage.skipped_updates}</dd></>}{finiteNumber(stage.loss) ? <><dt>Loss</dt><dd>{stage.loss.toPrecision(5)}</dd></> : stage.loss === null && <><dt>Loss</dt><dd>非有限，已省略</dd></>}{finiteNumber(stage.learning_rate) && <><dt>实际学习率</dt><dd>{stage.learning_rate.toExponential(3)}</dd></>}{finiteNumber(peak) && <><dt>实际训练显存峰值</dt><dd>{(peak / 2 ** 30).toFixed(2)} GiB</dd></>}</dl>{finiteNumber(value) && finiteNumber(target) && target > 0 && <><progress max={target} value={value} aria-label={(name === 's2' ? 'SoVITS' : 'GPT') + ' 阶段进度'}/><p className="muted small">{value} / {target} {epochMode ? '累计轮次' : '优化器更新'}</p></>}</> : <p className="muted small">等待工作进程写入真实计数。</p>}
  </article>;
}

export function TrainingRun({job, submit, jobs = []}: {job: Job; submit: Submit; jobs?: Job[]}) {
  const [view, setView] = useState<RunView>();
  const [loadError, setLoadError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [points, setPoints] = useState<CheckpointList>();
  const [pointsError, setPointsError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<'pause' | 'resume' | 'stop_export' | ''>('');
  const [controlError, setControlError] = useState('');
  const [controlNotice, setControlNotice] = useState('');
  const exportKey = jobs.filter(item => item.kind === 'model_export').map(item => `${item.id}:${item.state}`).join('|');
  useEffect(() => {setView(undefined); setLoadError(''); setPoints(undefined); setPointsError(''); setPendingAction(''); setControlError(''); setControlNotice('');}, [job.id]);
  useEffect(() => {
    let disposed = false;
    api<CheckpointList>('/training/' + encodeURIComponent(job.id) + '/checkpoints').then(next => {if (!disposed) {setPoints(next); setPointsError('');}}).catch(e => {if (!disposed) setPointsError(e.message);});
    return () => {disposed = true;};
  }, [job.id, job.state, refreshKey]);
  useEffect(() => {
    if (pendingAction === 'pause' && ['PAUSED', 'COMPLETED', 'FAILED', 'INTERRUPTED'].includes(job.state) || pendingAction === 'resume' && ['RUNNING', 'COMPLETED', 'FAILED', 'INTERRUPTED'].includes(job.state)) setPendingAction('');
    if (pendingAction === 'stop_export' && ['EARLY_COMPLETED', 'COMPLETED', 'FAILED', 'INTERRUPTED'].includes(job.state)) setPendingAction('');
  }, [job.state]);
  useEffect(() => {
    let disposed = false;
    const read = () => api<RunView>('/training/' + encodeURIComponent(job.id)).then(next => {if (!disposed) {setView(next); setLoadError('');}}).catch(e => {if (!disposed) setLoadError(e.message);});
    void read(); const timer = active(job) ? window.setInterval(() => void read(), 2500) : undefined;
    return () => {disposed = true; if (timer) window.clearInterval(timer);};
  }, [job.id, job.state, refreshKey, exportKey]);
  const stageState = view?.stage_state;
  const current = stageState ? stageState.active_stage : active(job) ? job.latest_progress?.stage : undefined;
  const merged = (stage: 's1' | 's2') => {
    if (!stageState?.stages?.[stage] && job.latest_progress?.stage !== stage) return undefined;
    const frozen = view?.run?.config?.[stage];
    return {target: frozen?.target, unit: frozen?.stop_mode === 'epochs' ? 'epochs' : 'optimizer_updates', ...stageState?.stages?.[stage], ...(job.latest_progress?.stage === stage ? job.latest_progress : {})};
  };
  const control = async (action: 'pause' | 'resume' | 'export' | 'stop_export') => {
    setBusy(true); setControlError('');
    try {await api('/training/' + encodeURIComponent(job.id) + '/controls', {...requestId(), action}); if (action === 'export') setControlNotice(active(job) ? '导出请求已受理，将在安全边界冻结权重对，训练继续；发布进度单独显示。' : '导出请求已受理，正在从已保存的完整权重对发布模型。'); else setPendingAction(action); setRefreshKey(value => value + 1);} catch (e) {setControlError((e as Error).message);} finally {setBusy(false);}
  };
  const derive = async (point: Checkpoint) => {
    if (!point.valid) return;
    setBusy(true); setControlError('');
    try {await submit('/training/' + encodeURIComponent(job.id) + '/derive', {checkpoint_id: point.id});} catch (e) {setControlError((e as Error).message);} finally {setBusy(false);}
  };
  const retryExport = async (path: string) => {
    setBusy(true); setControlError('');
    try {await submit(path, {}); setRefreshKey(value => value + 1);} catch (e) {setControlError((e as Error).message);} finally {setBusy(false);}
  };
  const waitingPause = pendingAction === 'pause' || view?.pending_controls?.some(command => command.action === 'pause');
  const waitingResume = pendingAction === 'resume' || view?.pending_controls?.some(command => command.action === 'resume');
  const waitingStop = pendingAction === 'stop_export' || view?.pending_controls?.some(command => command.action === 'stop_export');
  const waitingExport = view?.pending_controls?.some(command => command.action === 'export');
  const canResume = ['PAUSED', 'INTERRUPTED', 'FAILED', 'EARLY_COMPLETED'].includes(job.state) && Boolean(points?.last_valid);
  const exports = view?.exports?.map(item => ({...item, ...jobs.find(candidate => candidate.id === item.id)}));
  const parentRun = view?.run?.parent_run_id ?? view?.run?.parent_run;
  const parentPoint = view?.run?.parent_checkpoint_id ?? view?.run?.parent_checkpoint;
  return <div className="training-run">
    <div className="section-top"><h3>双阶段训练进度</h3><button className="quiet" onClick={() => setRefreshKey(value => value + 1)}>刷新运行快照与恢复点</button></div>
    <p className="muted small">每阶段独立计数，优化器更新与已处理 batch 分别记录。</p>
    {typeof parentRun === 'string' && <p className="lineage">派生自运行 {parentRun}{typeof parentPoint === 'string' ? ` · 恢复点 ${parentPoint}` : ''}</p>}
    {loadError && <p className="inline-error">运行快照读取失败：{loadError}</p>}
    <div className="grid"><RunStageCard name="s2" stage={merged('s2')} current={current === 's2'}/><RunStageCard name="s1" stage={merged('s1')} current={current === 's1'}/></div>
    <div className="actions training-controls">
      {['RUNNING', 'PAUSE_REQUESTED', 'CHECKPOINTING'].includes(job.state) && <button disabled={busy || Boolean(waitingPause || waitingStop) || job.state !== 'RUNNING'} onClick={() => void control('pause')}>暂停并保存恢复点</button>}
      {job.state === 'RUNNING' && <><button disabled={busy || Boolean(waitingPause || waitingStop || waitingExport)} onClick={() => void control('export')}>导出模型并继续训练</button><button disabled={busy || Boolean(waitingPause || waitingStop)} onClick={() => void control('stop_export')}>中止并导出模型</button></>}
      {['PAUSED', 'COMPLETED', 'EARLY_COMPLETED'].includes(job.state) && <button disabled={busy || Boolean(waitingResume)} onClick={() => void control('export')}>导出已保存权重对</button>}
      {['PAUSED', 'INTERRUPTED', 'FAILED', 'EARLY_COMPLETED'].includes(job.state) && <button className="primary-button" disabled={busy || !canResume || Boolean(waitingResume)} onClick={() => void control('resume')}>继续训练</button>}
    </div>
    {waitingPause && active(job) && <div className="notice"><strong>暂停请求已受理 · 等待安全边界</strong><p>工作进程将在优化器更新边界写入完整恢复点，成功后显示「已暂停」。请等待保存完成。</p></div>}
    {waitingPause && ['COMPLETED', 'EARLY_COMPLETED', 'CANCELLED'].includes(job.state) && <p className="muted small">任务已结束，无需继续等待暂停。请查看最终状态与恢复点记录。</p>}
    {waitingResume && <p className="muted small" role="status">继续训练请求已受理，正在校验恢复点并等待工作进程。</p>}
    {waitingStop && active(job) && <div className="notice"><strong>中止并导出请求已受理</strong><p>等待安全边界保存恢复点和权重快照，完成后停止后续训练并独立发布模型包。</p></div>}
    {controlNotice && <p className="muted small" role="status">{controlNotice}</p>}
    {job.state === 'PAUSED' && <p className="success-note">{canResume ? '训练已暂停，可从文件校验通过的恢复点申请续训至原累计目标。' : '训练已暂停，需确认有效恢复点后再继续。'}</p>}
    {controlError && <p className="inline-error" role="alert">{controlError}</p>}
    <details open><summary>模型导出 · {exports?.length ?? 0} 项</summary><p className="muted small">训练状态与发布状态独立。完成后自动导出；失败可从原快照重试，保持相同模型版本。</p>
      {view?.export_intents?.filter(intent => intent.state !== 'QUEUED').map(intent => {const id = intent.intent_id ?? intent.id; return <article className="checkpoint-row" key={id}><strong>{intent.state === 'FAILED' ? '冻结权重对后，导出登记失败' : '正在登记导出任务'}</strong><p className="muted small path-text">{exportReason(intent.reason)} · 意图 {id ?? '未记录'}{intent.checkpoint_id ? ` · 恢复点 ${intent.checkpoint_id}` : ''}</p>{intent.error && <p className="inline-error">{errorText(intent.error)}</p>}{intent.state === 'FAILED' && id && <button disabled={busy} onClick={() => void retryExport('/training/' + encodeURIComponent(job.id) + '/export-intents/' + encodeURIComponent(id) + '/retry')}>从原冻结快照重试登记</button>}</article>;})}
      {exports?.length ? <div className="checkpoint-list">{exports.map(item => <article className="checkpoint-row" key={item.id}><div className="section-top"><strong>{item.result?.status === 'PUBLISHED' ? '模型已发布' : stateLabel(item.state)}</strong><span className="pill">{exportReason(item.snapshot?.reason ?? String(item.payload?.reason ?? '权重快照'))}</span></div><p className="muted small path-text">导出任务 {item.id}<br/>快照 {String(item.payload?.snapshot_id ?? item.result?.snapshot_id ?? item.snapshot?.id ?? '等待登记')}<br/>模型版本 {String(item.payload?.model_version_id ?? item.result?.model_version_id ?? '等待登记')}</p>{item.error && <p className="inline-error">{errorText(item.error)}</p>}{item.state === 'FAILED' && <button disabled={busy} onClick={() => void retryExport('/model-exports/' + encodeURIComponent(item.id) + '/retry')}>从同一快照重试导出</button>}<details><summary>阶段来源与导出记录</summary><pre>{JSON.stringify({snapshot: item.snapshot, result: item.result}, null, 2)}</pre></details></article>)}</div> : <p className="muted small">尚未登记导出任务。运行中可请求导出，正常结束后自动发布。</p>}</details>
    <details open><summary>训练恢复点 · {points?.checkpoints.length ?? 0} 个</summary><p className="muted small">此处校验已登记文件的完整性；训练状态与配置兼容性由工作进程在恢复时继续验证。</p>
      {pointsError && <p className="inline-error">恢复点读取失败：{pointsError}</p>}
      {points && !points.last_valid && <p className="muted small">当前没有通过校验的恢复点，暂不能继续训练。</p>}
      {points?.inherited_start_point && <div className="notice"><strong>{points.inherited_start_point.valid === true ? '尚无子运行恢复点，将从已冻结父起点继续' : '已冻结父起点校验失败，暂不能继续'}</strong><p className="path-text">{points.inherited_start_point.stage?.toUpperCase()} · {points.inherited_start_point.id ?? '未找到恢复点'}</p>{points.inherited_start_point.selection?.source_run && <p className="path-text">父运行：{points.inherited_start_point.selection.source_run}</p>}{points.inherited_start_point.reason && <p className="inline-error">{points.inherited_start_point.reason}</p>}<p>该起点用于继续当前子运行，原父运行与恢复点保持不变。</p></div>}
      <div className="checkpoint-list">{points?.checkpoints.map(point => <article className="checkpoint-row" key={point.id}><div className="section-top"><strong>{point.stage.toUpperCase()} · {point.kind ?? '恢复点'}</strong><span className={'pill ' + (!point.valid ? 'checkpoint-invalid' : '')}>{point.valid === true ? '文件校验通过' : '文件校验失败'}</span></div><p className="muted small path-text">{point.id}</p><p className="muted small">{finiteNumber(point.metrics?.completed_epochs) ? `已完成 ${point.metrics.completed_epochs} 轮 · ` : ''}{point.metrics && finiteNumber(optimizerUpdates(point.metrics)) ? `优化器更新 ${optimizerUpdates(point.metrics)}` : '进度见恢复点记录'}{point.protected ? ' · 受引用保护' : ''}</p>{point.reason && <p className="inline-error">{point.reason}</p>}<button disabled={busy || !point.valid} onClick={() => void derive(point)}>从此恢复点派生</button><details><summary>恢复点详情</summary><pre>{JSON.stringify(point, null, 2)}</pre></details></article>)}</div>
      {Boolean(points?.invalid?.length) && <details><summary>未通过校验的恢复点记录 · {points?.invalid?.length}</summary><pre>{JSON.stringify(points?.invalid, null, 2)}</pre></details>}
    </details>
    <details><summary>冻结运行配置与阶段快照</summary><pre>{view ? JSON.stringify({run: view.run, stage_state: view.stage_state}, null, 2) : '等待读取运行记录'}</pre></details>
  </div>;
}

export function Training({jobs, submit, reportError, requestedVersion = '', refreshToken = 0}: {jobs: Job[]; submit: Submit; reportError: (message: string) => void; requestedVersion?: string; refreshToken?: number}) {
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [versionId, setVersionId] = useState('');
  const [initialModel, setInitialModel] = useState('official');
  const [s2, setS2] = useState(initialS2);
  const [s1, setS1] = useState(initialS1);
  const [stages, setStages] = useState<string[]>(['s2', 's1']);
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);
  const [preflightId, setPreflightId] = useState('');
  const [submittedConfig, setSubmittedConfig] = useState('');
  const [featureId, setFeatureId] = useState('');
  const [trainingId, setTrainingId] = useState('');
  const [versionError, setVersionError] = useState('');
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');
  const config = useMemo(() => ({version_id: versionId, initial_model: initialModel, stages, seed, s2, s1}), [versionId, initialModel, stages, seed, s2, s1]);
  const configKey = JSON.stringify(config);
  useEffect(() => {api<DatasetVersion[]>('/datasets').then(rows => {setVersions(rows); setVersionError('');}).catch(e => setVersionError(e.message));}, [jobsKey, requestedVersion, refreshToken]);
  useEffect(() => {if (requestedVersion) setVersionId(requestedVersion);}, [requestedVersion]);
  useEffect(() => {setPreflightId(''); setSubmittedConfig('');}, [configKey]);
  const version = versions.find(row => row.id === versionId);
  const preflightJob = jobs.find(job => job.id === preflightId);
  const featureJob = jobs.find(job => job.id === featureId);
  const trainingJob = jobs.find(job => job.id === trainingId);
  const running = Boolean(preflightJob && active(preflightJob) || featureJob && active(featureJob) || trainingJob && active(trainingJob));
  const report = submittedConfig === configKey && preflightJob?.state === 'COMPLETED' ? preflightJob.result as Preflight | undefined : undefined;
  const setStage = (name: string, enabled: boolean) => setStages(current => ['s2', 's1'].filter(stage => stage === name ? enabled : current.includes(stage)));
  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); reportError('');
    try {await operation();} catch (e) {reportError((e as Error).message);} finally {setBusy(false);}
  };
  const check = async () => {
    if (!version) throw new Error('请先选择已发布的数据版本。');
    if (!stages.length) throw new Error('至少选择一个训练阶段。');
    if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw new Error('随机种子需为 0–2147483647 的整数。');
    for (const stage of stages) {
      const current = stage === 's2' ? s2 : s1;
      if (!Number.isInteger(current.batch_size) || current.batch_size < 1 || current.batch_size > 64 || !Number.isInteger(current.target) || current.target < 1 || !Number.isFinite(current.learning_rate) || current.learning_rate <= 0 || current.learning_rate > .1) throw new Error('各阶段 batch 需为 1–64 的整数，累计目标需为正整数，学习率需在 (0, 0.1]。');
    }
    const job = await submit('/training/preflight', config);
    setSubmittedConfig(configKey); setPreflightId(job.id);
  };
  return <section className="panel training-panel" id="training-config"><div className="section-top"><div><p className="eyebrow">04 / TRAINING PREPARATION</p><h2>训练配置与预检</h2></div><span className="pill">SoVITS → GPT</span></div><p className="muted small">选择冻结的数据版本，准备上游特征，再由两个阶段的真实加载器检查可训练性。</p>
    {versionError && <p className="inline-error">数据版本读取失败：{versionError}</p>}
    <label>训练数据版本<select value={versionId} disabled={busy} onChange={e => setVersionId(e.target.value)}><option value="">选择已发布数据版本</option>{versions.map(row => <option key={row.id} value={row.id}>{row.role} · {row.id.slice(0, 8)} · {Array.isArray(row.clips) ? row.clips.length : row.clips} 段</option>)}</select></label>
    {version && <div className="feature-card"><div><strong>{version.feature_status?.toUpperCase() === 'READY' ? '训练特征已就绪' : '准备训练特征'}</strong><p>文本音素与 BERT → HuBERT 与训练音频 → 语义 token</p></div><button disabled={busy || running} onClick={() => void perform(async () => {setPreflightId(''); setSubmittedConfig(''); const job = await submit('/dataset-versions/' + encodeURIComponent(versionId) + '/features', {}); setFeatureId(job.id);})}>{version.feature_status?.toUpperCase() === 'READY' ? '校验并补齐特征' : '准备训练特征'}</button></div>}
    {featureJob && <p className="muted small">特征任务 {featureJob.id.slice(0, 8)} · {active(featureJob) ? '正在处理，可在后台任务查看进度' : featureJob.state === 'COMPLETED' ? '已完成' : errorText(featureJob.error) ?? featureJob.state}</p>}
    <div className="grid"><label>初始化权重<select value={initialModel} onChange={e => setInitialModel(e.target.value)}><option value="official">官方 GPT-SoVITS v4 底模</option></select></label><label>随机种子<input type="number" min="0" max="2147483647" step="1" value={seed} onChange={e => setSeed(e.target.valueAsNumber)}/></label></div>
    <StageFields name="s2" value={s2} enabled={stages.includes('s2')} onEnabled={enabled => setStage('s2', enabled)} onChange={setS2}/>
    <StageFields name="s1" value={s1} enabled={stages.includes('s1')} onEnabled={enabled => setStage('s1', enabled)} onChange={setS1}/>
    <div className="actions"><button disabled={busy || running || !version || !stages.length} onClick={() => void perform(check)}>检查训练配置与显存</button><button className="primary-button" disabled={busy || running || !version || !report?.ready || submittedConfig !== configKey} onClick={() => void perform(async () => {if (!version || !report?.ready || submittedConfig !== configKey) throw new Error('当前配置需要重新预检。'); const job = await submit('/training', {preflight_id: preflightId}); setTrainingId(job.id);})}>开始训练</button><p className="muted small">修改配置后需要重新预检。</p></div>
    {trainingJob && <p className="muted small">训练 {trainingJob.id.slice(0, 8)} · {stateLabel(trainingJob.state)}。冻结配置和双阶段进度请见后台任务。</p>}
    {preflightJob && active(preflightJob) && <div className="notice"><strong>正在构造实际训练加载器</strong><p>任务 {preflightJob.id.slice(0, 8)}，结果会自动显示。GPU 忙碌时按队列执行。</p></div>}
    {preflightJob?.error && <p className="inline-error">预检失败：{errorText(preflightJob.error)}。请在后台任务查看阶段和进程日志。</p>}
    {report && <div className="preflight-report"><div className={report.ready ? 'success-note' : 'alert'} role="status">{report.ready ? '数据与加载器预检通过' : '预检未通过 · 请处理以下阻塞项'}</div>{Boolean(report.blockers?.length) && <ul className="blockers">{report.blockers?.map((blocker, i) => <li key={i}>{errorText(blocker)}</li>)}</ul>}<div className="grid"><PreflightStage name="s2" result={report.stages?.s2}/><PreflightStage name="s1" result={report.stages?.s1}/></div><PreflightGpu gpu={report.gpu}/><details><summary>完整生效配置与预检记录</summary><pre>{JSON.stringify(report, null, 2)}</pre></details><p className="muted small path-text">配置指纹：{report.config_fingerprint}</p></div>}
  </section>;
}
