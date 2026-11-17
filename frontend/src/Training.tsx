import {useEffect, useMemo, useState} from 'react';
import {active, api, errorText, Job, JobError} from './api';
import type {DatasetVersion} from './Datasets';

type StageConfig = {batch_size: number; precision: string; stop_mode: 'steps' | 'epochs'; target: number; learning_rate: number; rank?: number; accumulate?: number};
type StageResult = {samples?: number; unique_samples?: number; batches?: number; batch_size?: number; precision?: string; [key: string]: unknown};
type Preflight = {config: Record<string, unknown>; config_fingerprint: string; stages: {s1?: StageResult; s2?: StageResult}; ready: boolean; blockers?: (string | JobError)[]; gpu?: {device?: string; free_bytes?: number; total_bytes?: number; peak_allocated_bytes?: number; probe?: string; training_memory?: string}};
type Submit = (path: string, payload: object) => Promise<Job>;
const initialS2: StageConfig = {batch_size: 1, precision: 'fp16', rank: 32, stop_mode: 'epochs', target: 1, learning_rate: .0001};
const initialS1: StageConfig = {batch_size: 1, precision: 'fp16', stop_mode: 'epochs', target: 1, learning_rate: .01, accumulate: 4};

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
    {typeof gpu.free_bytes === 'number' && <p>采样时可用显存 {(gpu.free_bytes / 2 ** 30).toFixed(2)} GiB{typeof gpu.total_bytes === 'number' ? ` / ${(gpu.total_bytes / 2 ** 30).toFixed(2)} GiB` : ''}</p>}
    {loaderOnly && typeof gpu.peak_allocated_bytes === 'number' && <p>数据张量搬运峰值 {(gpu.peak_allocated_bytes / 2 ** 20).toFixed(2)} MiB，仅覆盖加载器样本。</p>}
    {(loaderOnly || gpu.training_memory === 'NOT_MEASURED') && <p className="memory-limit"><strong>完整训练显存尚未实测</strong><br/>本次探针没有加载完整训练模型、优化器，也没有执行前向和反向计算。以上数值不能用于估算训练显存；实际峰值由真实训练记录。</p>}
  </div>;
}

export function Training({jobs, submit, reportError, requestedVersion = ''}: {jobs: Job[]; submit: Submit; reportError: (message: string) => void; requestedVersion?: string}) {
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
  const [versionError, setVersionError] = useState('');
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');
  const config = useMemo(() => ({version_id: versionId, initial_model: initialModel, stages, seed, s2, s1}), [versionId, initialModel, stages, seed, s2, s1]);
  const configKey = JSON.stringify(config);
  useEffect(() => {api<DatasetVersion[]>('/datasets').then(rows => {setVersions(rows); setVersionError('');}).catch(e => setVersionError(e.message));}, [jobsKey, requestedVersion]);
  useEffect(() => {if (requestedVersion) setVersionId(requestedVersion);}, [requestedVersion]);
  useEffect(() => {setPreflightId(''); setSubmittedConfig('');}, [configKey]);
  const version = versions.find(row => row.id === versionId);
  const preflightJob = jobs.find(job => job.id === preflightId);
  const featureJob = jobs.find(job => job.id === featureId);
  const running = Boolean(preflightJob && active(preflightJob) || featureJob && active(featureJob));
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
    <div className="actions"><button className="primary-button" disabled={busy || running || !version || !stages.length} onClick={() => void perform(check)}>检查训练配置与显存</button><p className="muted small">修改配置后需要重新预检。</p></div>
    {preflightJob && active(preflightJob) && <div className="notice"><strong>正在构造实际训练加载器</strong><p>任务 {preflightJob.id.slice(0, 8)}，结果会自动显示。GPU 忙碌时按队列执行。</p></div>}
    {preflightJob?.error && <p className="inline-error">预检失败：{errorText(preflightJob.error)}。请在后台任务查看阶段和进程日志。</p>}
    {report && <div className="preflight-report"><div className={report.ready ? 'success-note' : 'alert'} role="status">{report.ready ? '数据与加载器预检通过' : '预检未通过 · 请处理以下阻塞项'}</div>{Boolean(report.blockers?.length) && <ul className="blockers">{report.blockers?.map((blocker, i) => <li key={i}>{errorText(blocker)}</li>)}</ul>}<div className="grid"><PreflightStage name="s2" result={report.stages?.s2}/><PreflightStage name="s1" result={report.stages?.s1}/></div><PreflightGpu gpu={report.gpu}/><details><summary>完整生效配置与预检记录</summary><pre>{JSON.stringify(report, null, 2)}</pre></details><p className="muted small path-text">配置指纹：{report.config_fingerprint}</p></div>}
  </section>;
}
