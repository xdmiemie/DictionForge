import {useCallback, useEffect, useState} from 'react';
import {active, api, errorText, finiteNumber, Job, JobError, kindLabel, requestId, stateLabel} from './api';
import type {ModelVersion} from './Models';
import {TextSynthesis} from './TextSynthesis';

export type ReferenceAudio = {id?: string; audio_id?: string; path?: string; sha256?: string; text: string; language: string; seconds: number; sample_rate?: number; channels?: number; fingerprint?: string; status?: string};
type Selection = {active_model: string | null; model?: ModelVersion | null; default_reference?: ReferenceAudio | null; reference_fingerprint?: string | null; enabled: boolean; valid: boolean; errors?: string[]; gpu_resident?: boolean};
type SynthesisSystem = {gpu?: string | Record<string, unknown>; gpu_jobs?: Job[]; gpu_resident_models?: {job_id: string; model_id: string; stage?: string}[]};
type Parameters = {seed?: number; sample_steps?: number; speed_factor?: number; [key: string]: unknown};
type AudioResult = {audio_id?: string; seconds?: number; sample_rate?: number; sha256?: string; samples?: number};
type ProbeResult = {audio?: AudioResult; model_id?: string; model_version_id?: string; text?: string; conditions_fingerprint?: string; parameters?: Parameters; error?: string | JobError; metrics?: {peak_cuda_bytes?: number; total_seconds?: number}};
type Comparison = {id?: string; comparison_id?: string; model_id?: string; model_version_id?: string; created?: number; jobs: Job[]; reference?: ReferenceAudio; parameters?: Parameters; text: string; conditions_fingerprint?: string; reference_fingerprint?: string; state?: string; error?: string | JobError};
type Submit = (path: string, payload: object) => Promise<Job>;
const fixedText = '你好，欢迎来到声句工坊。';
const audioUrl = (id: string) => '/api/audio/' + encodeURIComponent(id);

export function ReferencePreview({reference, title = '当前默认参考'}: {reference?: ReferenceAudio | null; title?: string}) {
  return <div className="reference-preview"><div className="section-top"><h3>{title}</h3>{reference && <span className="pill">{reference.language === 'zh' ? '中文 · 普通话' : reference.language}</span>}</div>{reference ? <>
    <p className="reference-transcript">{reference.text || '缺少准确文本'}</p>
    <p className="muted small">{finiteNumber(reference.seconds) ? `${reference.seconds.toFixed(2)} 秒` : '时长未报告'}{finiteNumber(reference.sample_rate) ? ` · ${(reference.sample_rate / 1000).toFixed(0)} kHz` : ''}{reference.id ? ` · ${reference.id.slice(0, 8)}` : ''}</p>
    {reference.audio_id ? <audio controls preload="none" src={audioUrl(reference.audio_id)}/> : <p className="inline-error">参考尚未登记可播放音频，请重新设置参考。</p>}
  </> : <p className="muted small">尚未设置参考。请选择或上传 3–10 秒、带准确文本的清晰语音。</p>}</div>;
}

export function ComparisonAudioCard({job, label}: {job?: Job; label: string}) {
  const result = job?.result as ProbeResult | undefined;
  const audio = result?.audio;
  return <article className="comparison-card"><div className="section-top"><h3>{label}</h3><span className="pill">{job ? stateLabel(job.state) : '等待任务'}</span></div>
    {audio?.audio_id && job?.state === 'COMPLETED' ? <><audio controls preload="none" src={audioUrl(audio.audio_id)}/><p className="muted small">{finiteNumber(audio.sample_rate) ? `${audio.sample_rate / 1000} kHz` : '采样率未报告'}{finiteNumber(audio.seconds) ? ` · ${audio.seconds.toFixed(2)} 秒` : ''}</p></> : <p className="muted small">{job && active(job) ? '排队或生成中，结果将自动显示。' : job?.state === 'FAILED' ? '生成失败，可在后台任务查看日志。' : '等待可播放结果。'}</p>}
    {(job?.error || result?.error) && <p className="inline-error">{errorText(job?.error ?? result?.error)}</p>}
    {job && <details><summary>模型与实际推理记录</summary><p className="path-text small">任务 {job.id}<br/>模型 {String(result?.model_version_id ?? result?.model_id ?? job.payload?.model_version_id ?? '等待登记')}</p><pre>{JSON.stringify({parameters: result?.parameters, conditions_fingerprint: result?.conditions_fingerprint, audio, metrics: result?.metrics}, null, 2)}</pre></details>}
  </article>;
}

export function Synthesis({jobs, submit, reportError, refreshToken = 0, onCatalogChange, system}: {jobs: Job[]; submit: Submit; reportError: (message: string) => void; refreshToken?: number; onCatalogChange: () => void; system?: SynthesisSystem}) {
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [selection, setSelection] = useState<Selection>();
  const [references, setReferences] = useState<ReferenceAudio[]>([]);
  const [modelId, setModelId] = useState('');
  const [referenceId, setReferenceId] = useState('');
  const [file, setFile] = useState<File>();
  const [fileUrl, setFileUrl] = useState('');
  const [referenceText, setReferenceText] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingError, setLoadingError] = useState('');
  const [notice, setNotice] = useState('');
  const [comparison, setComparison] = useState<Comparison>();
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [comparisonId, setComparisonId] = useState(() => {try {return localStorage.getItem('dictionforge.comparison') ?? '';} catch {return '';}});
  const [lookupId, setLookupId] = useState('');
  const [probeId, setProbeId] = useState('');
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');
  const load = useCallback(async () => {
    const [nextModels, nextSelection, nextReferences, nextComparisons] = await Promise.all([api<ModelVersion[]>('/models'), api<Selection>('/synthesis/selection'), api<ReferenceAudio[]>('/references'), api<Comparison[]>('/comparisons')]);
    setModels(nextModels); setSelection(nextSelection); setReferences(nextReferences); setComparisons(nextComparisons); setLoadingError('');
    setModelId(current => current && nextModels.some(model => model.id === current) ? current : nextSelection.active_model ?? nextModels.find(model => model.id !== 'official')?.id ?? 'official');
  }, []);
  useEffect(() => {void load().catch(error => setLoadingError(error.message));}, [load, jobsKey, refreshToken]);
  useEffect(() => {
    if (!file) {setFileUrl(''); return;}
    const url = URL.createObjectURL(file); setFileUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    if (!comparisonId) return;
    try {localStorage.setItem('dictionforge.comparison', comparisonId);} catch {/* Persistence is optional. */}
    let disposed = false; let timer: ReturnType<typeof window.setTimeout> | undefined;
    const refresh = () => api<Comparison>('/comparisons/' + encodeURIComponent(comparisonId)).then(value => {if (!disposed) {setComparison(value); if (value.jobs.some(active)) timer = window.setTimeout(() => void refresh(), 2500);}}).catch(error => {if (!disposed) setLoadingError(error.message);});
    setComparison(undefined); void refresh();
    return () => {disposed = true; if (timer) window.clearTimeout(timer);};
  }, [comparisonId]);
  const model = models.find(value => value.id === modelId);
  const defaultReference = model?.default_reference as ReferenceAudio | null | undefined;
  const managedReference = references.find(value => value.id === referenceId);
  const enabledHere = selection?.enabled && selection.active_model === modelId;
  const ready = Boolean(defaultReference?.text?.trim() && defaultReference.language === 'zh' && finiteNumber(defaultReference.seconds) && defaultReference.seconds >= 3 && defaultReference.seconds <= 10 && defaultReference.audio_id);
  const comparedJobs = comparison?.jobs.map(job => ({...job, ...jobs.find(value => value.id === job.id)})) ?? [];
  const comparing = comparedJobs.some(active);
  const previousReference = comparison?.reference;
  const comparisonModelReference = models.find(value => value.id === (comparison?.model_id ?? comparison?.model_version_id))?.default_reference as ReferenceAudio | null | undefined;
  const referenceChanged = Boolean(previousReference && comparisonModelReference && (previousReference.sha256 !== comparisonModelReference.sha256 || previousReference.text !== comparisonModelReference.text || previousReference.language !== comparisonModelReference.language));
  const probe = jobs.find(job => job.id === probeId);
  const device = typeof system?.gpu === 'string' ? system.gpu.split(',').map(value => value.trim()) : undefined;
  const memoryReported = device?.length === 4 && Number.isFinite(Number(device[2])) && Number.isFinite(Number(device[3]));
  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); reportError(''); setNotice('');
    try {await operation();} catch (error) {reportError((error as Error).message);} finally {setBusy(false);}
  };
  const setDefault = async (id: string) => {
    await api('/models/' + encodeURIComponent(modelId) + '/reference', {...requestId(), reference_id: id});
    await load(); onCatalogChange(); setNotice('默认参考已更新，后续试听将使用新素材与文本。');
  };
  const upload = async () => {
    if (!file || !referenceText.trim()) throw new Error('请选择参考音频，并填写音频实际说出的文本。');
    if (file.size > 25 * 1024 ** 2) throw new Error('参考文件最大 25 MiB，请选择较小的 3–10 秒片段。');
    const query = new URLSearchParams({...requestId(), name: file.name, text: referenceText.trim(), language: 'zh'});
    const response = await fetch('/api/references/upload?' + query, {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
    const result = await response.json();
    if (!response.ok) {const value = result.error ?? result.detail ?? result; throw new Error(typeof value === 'string' ? value : value.message ?? JSON.stringify(value));}
    const reference = result as ReferenceAudio;
    if (!reference.id) throw new Error('上传结果未包含参考素材编号。');
    setReferenceId(reference.id); await setDefault(reference.id);
  };
  return <section className="panel synthesis-panel"><div className="section-top"><div><p className="eyebrow">VOICE / REFERENCE / LISTEN</p><h2>音色与效果试听</h2></div><span className="pill">原生 48 kHz</span></div>
    <p className="muted small">选择音色，确认参考语音，再用相同条件对比自训模型与官方底模。</p>
    {loadingError && <p className="inline-error">试听工作区读取失败：{loadingError}</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    <label>选择模型版本<select aria-label="选择模型版本" value={modelId} disabled={busy} onChange={event => setModelId(event.target.value)}>{models.map(item => <option key={item.id} value={item.id}>{item.role} · {item.id === 'official' ? '官方底模' : item.id.slice(0, 8)}</option>)}</select></label>
    <div className="synthesis-status"><div><span>逻辑启用</span><strong>{selection?.enabled ? `${selection.model?.role ?? '模型'} · ${selection.active_model === 'official' ? '官方' : selection.active_model?.slice(0, 8)}` : '尚未启用模型'}</strong><small>{enabledHere ? '当前查看的模型已启用' : '选择列表用于查看，启用后生效'}</small></div><div><span>GPU 实际任务（本应用）</span><strong>{system?.gpu_jobs ? system.gpu_jobs.length ? system.gpu_jobs.map(job => kindLabel(job.kind)).join('、') : '本应用暂无 GPU 任务' : '状态尚未报告'}</strong><small>{system?.gpu_jobs?.length ? '运行中的任务持有本应用 GPU 租约' : '模型按任务加载，逻辑启用不常驻 GPU'}</small></div></div>
    <p className="muted small path-text">本应用实际驻留推理模型：{system?.gpu_resident_models ? system.gpu_resident_models.length ? system.gpu_resident_models.map(item => `${item.model_id}（${item.stage ?? '推理'}）`).join('、') : '未驻留模型' : '等待状态报告'}</p>
    {memoryReported ? <p className="hardware-memory small">{device![0]} · 设备可用显存 {(Number(device![3]) / 1024).toFixed(2)} / {(Number(device![2]) / 1024).toFixed(2)} GiB<span className="muted">（包含其他程序的实际占用）</span></p> : system?.gpu && <details><summary>设备与显存采样</summary><pre>{typeof system.gpu === 'string' ? system.gpu : JSON.stringify(system.gpu, null, 2)}</pre></details>}
    {selection?.enabled && !selection.valid && <p className="inline-error">已启用模型需要重新检查：{selection.errors?.join('；') ?? '参考或模型不可用'}</p>}
    <div className="actions"><button className="primary-button" disabled={busy || !model || !ready || Boolean(enabledHere && selection?.valid)} onClick={() => void perform(async () => {const next = await api<Selection>('/models/' + encodeURIComponent(modelId) + '/enable', requestId()); setSelection(next); setNotice('模型已逻辑启用，GPU 将在实际试听任务启动时加载。');})}>{enabledHere ? selection?.valid ? '已启用当前模型' : '重新校验并启用' : '启用此模型'}</button><button disabled={busy || !selection?.enabled} onClick={() => void perform(async () => {setSelection(await api<Selection>('/models/disable', requestId())); setNotice('已停用模型选择。');})}>停用模型</button><button className="quiet" disabled={busy} onClick={() => void perform(load)}>刷新模型与参考</button></div>
    {!ready && <p className="muted small">启用前需要有效的中文参考：3–10 秒、非空准确文本。上传后会进行音频内容检查。</p>}
    <ReferencePreview reference={defaultReference}/>
    <details className="reference-editor"><summary>选择或替换默认参考</summary><label>已管理参考<select aria-label="已管理参考" value={referenceId} disabled={busy} onChange={event => setReferenceId(event.target.value)}><option value="">选择已有参考素材</option>{references.map(item => <option key={item.id} value={item.id}>{item.text.slice(0, 36)} · {item.seconds.toFixed(2)} 秒 · {item.id?.slice(0, 8)}</option>)}</select></label>{managedReference && <ReferencePreview reference={managedReference} title="待设为默认的参考"/>}<button disabled={busy || !model || !managedReference?.id} onClick={() => void perform(() => setDefault(managedReference!.id!))}>设为默认参考</button>
      <div className="reference-upload"><h3>从本机上传参考</h3><p className="muted small">上传 3–10 秒清晰语音（最大 25 MiB），并填写其中实际说出的内容。</p><label>参考音频文件<input aria-label="参考音频文件" type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg,.aac,.wma,.aif,.aiff" disabled={busy} onChange={event => setFile(event.target.files?.[0])}/></label>{fileUrl && <audio controls preload="metadata" src={fileUrl}/>}<label>参考音频准确文本<textarea aria-label="参考音频准确文本" maxLength={1000} value={referenceText} disabled={busy} placeholder="与参考音频中实际朗读的文字一致" onChange={event => setReferenceText(event.target.value)}/></label><p className="muted small">参考语言：中文 · 普通话</p><button disabled={busy || !model || !file || !referenceText.trim()} onClick={() => void perform(upload)}>上传并设为默认参考</button></div>
    </details>
    <TextSynthesis modelId={modelId} canGenerate={Boolean(ready && enabledHere && selection?.valid)} reference={defaultReference} jobs={jobs} submit={submit} reportError={reportError}/>
    <div className="fixed-probe"><div className="section-top"><h3>固定短句效果试听</h3><span className="pill">相同条件比较</span></div><blockquote>{fixedText}</blockquote><p className="muted small">同一参考与语言 · 种子 42 · 采样步数 32 · 语速 1.0。两次生成按 GPU 队列顺序执行。</p><div className="actions"><button className="primary-button" disabled={busy || !ready || !enabledHere || !selection?.valid || modelId === 'official' || comparing} onClick={() => void perform(async () => {const next = await api<Comparison>('/models/' + encodeURIComponent(modelId) + '/compare', requestId()); const id = next.comparison_id ?? next.id; if (!id) throw new Error('比较请求未返回比较编号。'); setComparison(next); setComparisonId(id); setNotice('对比试听已进入 GPU 队列，两份结果使用同一冻结条件。');})}>生成自训与官方对比试听</button><button disabled={busy || !ready || !enabledHere || !selection?.valid || Boolean(probe && active(probe))} onClick={() => void perform(async () => {const job = await submit('/models/' + encodeURIComponent(modelId) + '/probe', {}); setProbeId(job.id);})}>试听当前模型</button></div>
      {probe && <ComparisonAudioCard job={probe} label="当前模型固定短句"/>}
      {comparison && <div className="comparison-results"><div className="section-top"><h3>对比试听结果</h3><span className="pill">{comparison.state ? stateLabel(comparison.state) : comparedJobs.some(active) ? '生成中' : '已读取任务状态'}</span></div><p className="muted small path-text">历史比较模型：{comparison.model_id ?? comparison.model_version_id ?? '未报告'}</p>{comparison.error && <p className="inline-error">比较未通过：{errorText(comparison.error)}</p>}<p className="reference-transcript">{comparison.text}</p>{referenceChanged && <p className="notice small">此来源模型的默认参考已更换。以下保留历史比较结果；再次生成将使用新参考条件。</p>}<p className="muted small">实际种子 {comparison.parameters?.seed ?? '未报告'} · 采样步数 {comparison.parameters?.sample_steps ?? '未报告'} · 语速 {comparison.parameters?.speed_factor ?? '未报告'}</p><div className="comparison-grid">{comparedJobs.map((job, index) => <ComparisonAudioCard key={job.id} job={job} label={job.payload?.model_version_id === 'official' ? '官方底模' : index === 0 ? '自训模型' : '模型试听'}/>)}</div><details><summary>冻结的参考与比较条件</summary><pre>{JSON.stringify({id: comparison.comparison_id ?? comparison.id, reference: comparison.reference, parameters: comparison.parameters, conditions_fingerprint: comparison.conditions_fingerprint}, null, 2)}</pre></details></div>}
      <label>历史对比试听<select aria-label="历史对比试听" value={comparisonId} onChange={event => {setComparisonId(event.target.value); if (!event.target.value) setComparison(undefined);}}><option value="">选择已有比较记录</option>{comparisons.map(item => {const id = item.comparison_id ?? item.id; return <option key={id} value={id}>{item.created ? new Date(item.created * 1000).toLocaleString('zh-CN') + ' · ' : ''}{id?.slice(0, 8)} · {item.state ? stateLabel(item.state) : '比较记录'}</option>;})}</select></label>
      <details><summary>按编号读取比较记录</summary><div className="path-input"><input aria-label="比较记录编号" placeholder="比较记录编号" value={lookupId} onChange={event => setLookupId(event.target.value)}/><button disabled={busy || !lookupId.trim()} onClick={() => void perform(async () => {const next = await api<Comparison>('/comparisons/' + encodeURIComponent(lookupId.trim())); setComparison(next); setComparisonId(next.comparison_id ?? next.id ?? lookupId.trim());})}>读取记录</button></div></details>
    </div>
  </section>;
}
