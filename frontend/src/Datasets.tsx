import {ReviewWizard, type ReviewChange} from './ReviewWizard';
import {PaginatedList} from './Pagination';
import {useCallback, useEffect, useRef, useState} from 'react';
import {active, api, errorText, Job, JobError, requestId} from './api';

type ScanFile = {id: string; name: string; path: string; seconds?: number; status?: string; reason?: string; audio_id?: string};
type Scan = {scan_id: string; role?: string; files: ScanFile[]; seconds?: number};
type Clip = {id: string; audio_id: string; text: string; language: string; reviewed: boolean; excluded: boolean; asr_error?: string | JobError | null; seconds: number};
type Draft = {id: string; role: string; revision: number; clips: Clip[]; published?: string; asr_error?: string | JobError | null};
type DraftEntry = {id: string; role: string; revision: number; clips?: number | Clip[]};
export type DatasetVersion = {id: string; dataset_id: string; role: string; seconds: number; clips: number | Clip[]; feature_status: string};
type Submit = (path: string, payload: object) => Promise<Job>;

const seconds = (value?: number) => Number.isFinite(value) ? `${Number(value).toFixed(1)} 秒` : '—';
const playable = (id?: string) => id ? '/api/audio/' + encodeURIComponent(id) : undefined;
const fileStatus = (file: ScanFile) => file.reason || ({valid: '可用', ready: '可用', ok: '可用', invalid: '不可用', duplicate: '重复', silent: '静音', damaged: '文件损坏', short: '时长过短'}[file.status?.toLowerCase() ?? ''] ?? file.status ?? '可用');

export function Datasets({jobs, submit, reportError, onPublished, refreshToken = 0}: {jobs: Job[]; submit: Submit; reportError: (message: string) => void; onPublished?: (versionId: string) => void; refreshToken?: number}) {
  const [source, setSource] = useState('');
  const [role, setRole] = useState('');
  const [language, setLanguage] = useState('zh');
  const [recursive, setRecursive] = useState(true);
  const [scanJobId, setScanJobId] = useState('');
  const [excluded, setExcluded] = useState<string[]>([]);
  const [minSeconds, setMinSeconds] = useState(1);
  const [maxSeconds, setMaxSeconds] = useState(12);
  const [silenceDb, setSilenceDb] = useState(-40);
  const [silenceSeconds, setSilenceSeconds] = useState(.4);
  const [drafts, setDrafts] = useState<DraftEntry[]>([]);
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [draft, setDraft] = useState<Draft>();
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingDraftJob, setPendingDraftJob] = useState('');
  const [message, setMessage] = useState('');
  const draftRequest = useRef(0);
  const dirtyRef = useRef(false);
  useEffect(() => {if (!draft) {setDirty(false); dirtyRef.current = false;}}, [draft?.id]);
  const scanJob = jobs.find(job => job.id === scanJobId);
  const scan = scanJob?.state === 'COMPLETED' ? scanJob.result as Scan | undefined : undefined;
  const recentScans = jobs.filter(job => job.kind === 'dataset_scan' && job.state === 'COMPLETED' && job.result?.scan_id);
  const pendingJob = jobs.find(job => job.id === pendingDraftJob);
  const workRunning = Boolean(scanJob && active(scanJob) || pendingJob && active(pendingJob));
  const draftLocked = busy || workRunning || Boolean(draft?.published);
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');

  const refreshLists = useCallback(async () => {
    const result = await Promise.allSettled([api<DraftEntry[]>('/dataset-drafts'), api<DatasetVersion[]>('/datasets')]);
    if (result[0].status === 'fulfilled') {const rows=result[0].value;setDrafts(rows);setDraft(current=>current&&!rows.some(row=>row.id===current.id)?undefined:current);}
    if (result[1].status === 'fulfilled') setVersions(result[1].value);
  }, []);
  useEffect(() => {void refreshLists();}, [jobsKey, refreshLists, refreshToken]);
  const loadDraft = useCallback(async (id: string) => {
    const ticket = ++draftRequest.current;
    const loaded = await api<Draft>('/dataset-drafts/' + encodeURIComponent(id));
    if (ticket !== draftRequest.current) return;
    setDraft(loaded); setDirty(false); dirtyRef.current = false;
  }, []);
  useEffect(() => {
    if (pendingJob?.state !== 'COMPLETED') return;
    const id = pendingJob.result?.draft_id;
    setPendingDraftJob('');
    if (typeof id === 'string' && !dirtyRef.current) void loadDraft(id).then(() => setMessage('整理完成，请逐段试听并确认标注。')).catch(e => reportError(e.message));
    else setMessage('后台处理已完成。请保存当前修改后，重新读取相应草稿。');
  }, [pendingJob, loadDraft, reportError]);

  const run = async (operation: () => Promise<void>) => {
    setBusy(true); reportError(''); setMessage('');
    try {await operation();} catch (e) {reportError((e as Error).message);} finally {setBusy(false);}
  };
  const chooseDraft = (id: string) => {
    if (dirty && !window.confirm('当前草稿有未保存修改。切换草稿将放弃这些修改，是否继续？')) return;
    if (!id) {setDraft(undefined); setDirty(false); dirtyRef.current = false; return;}
    void run(() => loadDraft(id));
  };
  const updateClip = (id: string, change: Partial<Clip>) => {
    setDraft(value => value && {...value, clips: value.clips.map(clip => clip.id === id ? {...clip, ...change} : clip)});
    setDirty(true); dirtyRef.current = true;
  };
  const saveDraft = async () => {
    if (!draft) throw new Error('请先选择标注草稿。');
    if (!dirty) return draft;
    const saved = await api<Draft>('/dataset-drafts/' + encodeURIComponent(draft.id), {revision: draft.revision, clips: draft.clips.map(({id, text, language, reviewed, excluded}) => ({id, text, language, reviewed, excluded}))}, 'PATCH');
    setDraft(saved); setDirty(false); dirtyRef.current = false; return saved;
  };
  const prepare = async () => {
    if (!scan) return;
    if (!(minSeconds >= .5 && maxSeconds >= minSeconds && maxSeconds <= 30 && silenceSeconds >= .1 && silenceSeconds <= 3 && silenceDb >= -80 && silenceDb <= -10)) throw new Error('切分时长需在 0.5–30 秒，最短不超过最长；静音阈值为 -80 至 -10 dB，最短停顿为 0.1–3 秒。');
    const job = await submit('/datasets/prepare', {scan_id: scan.scan_id, excluded, min_seconds: minSeconds, max_seconds: maxSeconds, silence_db: silenceDb, silence_seconds: silenceSeconds});
    setPendingDraftJob(job.id); setMessage('整理任务已受理，可在后台查看切分与语音识别进度。');
  };
  const publish = async () => {
    const saved = await saveDraft();
    const version = await api<DatasetVersion>('/dataset-drafts/' + encodeURIComponent(saved.id) + '/publish', {revision: saved.revision});
    await loadDraft(saved.id);
    onPublished?.(version.id);
    await refreshLists(); setMessage(`已发布 ${version.role ?? saved.role} 的新数据版本 ${version.id.slice(0, 8)}。`);
  };
  const retryASR = async (ids: string[]) => {
    const saved = await saveDraft();
    const job = await submit('/dataset-drafts/' + encodeURIComponent(saved.id) + '/asr', {clip_ids: ids});
    setPendingDraftJob(job.id); setMessage('已提交逐段重新识别，完成后将读取最新草稿。');
  };
  const reviseVersion = async (version: DatasetVersion) => {
    if (dirty && !window.confirm('当前草稿有未保存修改。打开新草稿将放弃这些修改，是否继续？')) return;
    const created = await api<Draft>('/dataset-versions/' + encodeURIComponent(version.id) + '/draft', requestId());
    setDraft(created); setDirty(false); dirtyRef.current = false;
    await refreshLists(); setMessage('已从已发布版本建立新草稿。修改并发布后会生成新版本，原版本保留。');
  };
  const pendingClips = draft?.clips.filter(clip => !clip.excluded && (!clip.reviewed || !clip.text.trim())) ?? [];
  const commitReview = async (changes: ReviewChange[]) => {
    if (!draft) return;
    setBusy(true);
    try {
      const clips=draft.clips.map(clip=>({...clip,...changes.find(change=>change.id===clip.id)}));
      const saved=await api<Draft>('/dataset-drafts/'+encodeURIComponent(draft.id),{revision:draft.revision,clips:clips.map(({id,text,language,reviewed,excluded})=>({id,text,language,reviewed,excluded}))},'PATCH');
      setDraft(saved);setDirty(false);dirtyRef.current=false;
    } finally {setBusy(false);}
  };

  return <>
    <section className="panel dataset-panel"><div className="section-top"><div><p className="eyebrow">01 / RECORDINGS</p><h2>录音数据</h2></div><span className="pill">从角色音频目录开始</span></div>
      <label>服务电脑上的录音目录<div className="path-input"><input value={source} placeholder="例如 E:\角色录音\中文素材" onChange={e => {setSource(e.target.value); setScanJobId(''); setExcluded([]);}}/><button disabled={busy} onClick={() => void run(async () => {const result = await api<{path: string | null}>('/folders/pick', {initial: source}); if (result.path) {setSource(result.path); setScanJobId(''); setExcluded([]);}})}>浏览服务电脑</button></div></label>
      <div className="grid"><label>角色名称<input value={role} placeholder="为这组声音起一个名字" onChange={e => setRole(e.target.value)}/></label><label>识别语言<select value={language} onChange={e => setLanguage(e.target.value)}><option value="zh">中文 · 普通话</option></select></label></div>
      <label className="check"><input type="checkbox" checked={recursive} onChange={e => setRecursive(e.target.checked)}/>扫描子目录</label>
      <div className="actions"><button className="primary-button" disabled={busy || workRunning || !source.trim() || !role.trim()} onClick={() => void run(async () => {const job = await submit('/datasets/scan', {source, role: role.trim(), language, recursive}); setScanJobId(job.id); setExcluded([]);})}>扫描录音</button><p className="muted small">先检查素材，再选择要保留的录音。</p></div>
      {recentScans.length > 0 && <label>扫描记录<select aria-label="扫描记录" value={scanJobId} onChange={e => {setScanJobId(e.target.value); setExcluded([]);}}><option value="">选择一次扫描</option>{recentScans.map(job => <option key={job.id} value={job.id}>{String(job.payload?.role ?? '录音')} · {job.id.slice(0, 8)}</option>)}</select></label>}
      {scan?.files && <div className="scan-report"><div className="data-summary"><span><strong>{scan.files.length}</strong> 个文件</span><span><strong>{seconds(scan.seconds)}</strong> 扫描可用时长</span><span><strong>{excluded.length}</strong> 项手工排除</span></div><details open><summary>逐文件试听和排除</summary><div className="scan-files"><PaginatedList items={scan.files ?? []} label="扫描文件" key={scan.scan_id}>{file => <article className="scan-file" key={file.id}><div className="section-top"><div><strong>{file.name ?? file.path}</strong><p className="muted small">{fileStatus(file)} · {seconds(file.seconds)}</p></div><label className="check"><input type="checkbox" checked={excluded.includes(file.id)} onChange={e => setExcluded(value => e.target.checked ? [...value, file.id] : value.filter(id => id !== file.id))}/>排除</label></div>{file.audio_id && <audio controls preload="none" src={playable(file.audio_id)}/>}<details><summary>源文件路径</summary><p className="muted small path-text">{file.path}</p></details></article>}</PaginatedList></div></details>
        <details><summary>语音切分设置</summary><div className="grid"><label>最短片段（秒）<input type="number" min="0.5" max="30" step="0.1" value={minSeconds} onChange={e => setMinSeconds(e.target.valueAsNumber)}/></label><label>最大片段（秒）<input type="number" min="0.5" max="30" step="0.1" value={maxSeconds} onChange={e => setMaxSeconds(e.target.valueAsNumber)}/></label><label>静音阈值（dB）<input type="number" min="-80" max="-10" value={silenceDb} onChange={e => setSilenceDb(e.target.valueAsNumber)}/></label><label>最短停顿（秒）<input type="number" min="0.1" max="3" step="0.05" value={silenceSeconds} onChange={e => setSilenceSeconds(e.target.valueAsNumber)}/></label></div></details>
        <button className="primary-button" disabled={busy || workRunning || !scan.files.some(file => file.status === 'ready' && !excluded.includes(file.id))} onClick={() => void run(prepare)}>整理数据集 · 切分并转写</button>
      </div>}
    </section>
    {message && <div className="success-note" role="status">{message}</div>}
    <section className="panel"><div className="section-top"><div><p className="eyebrow">02 / REVIEW</p><h2>标注草稿</h2></div><button className="quiet" disabled={busy} onClick={() => void run(refreshLists)}>刷新列表</button></div><p className="muted small">输入纯语音即可：整理时自动切分并进行 STT 语音识别，随后选择逐条审核或全部默认通过。</p>
      <label>选择草稿<select aria-label="选择草稿" value={draft?.id ?? ''} disabled={busy} onChange={e => chooseDraft(e.target.value)}><option value="">选择整理后的草稿</option>{drafts.map(row => <option key={row.id} value={row.id}>{row.role} · {row.id.slice(0, 8)} · 修订 {row.revision}</option>)}</select></label>
      {draft ? <>{draft.published && <div className="success-note">此草稿已发布为版本 {draft.published.slice(0, 8)}，标注已冻结。</div>}{draft.asr_error && <p className="inline-error">语音识别未完成：{errorText(draft.asr_error)}。可逐段重试或手工填写。</p>}<div className="data-summary"><span><strong>{draft.clips.length}</strong> 段</span><span><strong>{pendingClips.length}</strong> 待处理</span><span>修订 {draft.revision}{dirty ? ' · 有未保存修改' : ' · 已保存'}</span></div><ReviewWizard key={draft.id} clips={draft.clips} locked={draftLocked} published={Boolean(draft.published)} onChange={updateClip} onCommit={commitReview}/>
        <div className="actions review-actions"><button disabled={draftLocked || !dirty} onClick={() => void run(async () => {await saveDraft(); setMessage('标注修改已保存。');})}>保存标注修改</button><button className="primary-button" disabled={draftLocked || pendingClips.length > 0 || !draft.clips.some(clip => !clip.excluded)} onClick={() => void run(publish)}>确认并发布数据版本</button><button className="quiet" disabled={draftLocked || !pendingClips.length} onClick={() => void run(() => retryASR(pendingClips.map(clip=>clip.id)))}>重新识别待审核片段</button><button className="quiet" disabled={draftLocked} onClick={() => chooseDraft(draft.id)}>重新读取草稿</button></div><p className="muted small">{pendingClips.length ? `还有 ${pendingClips.length} 段需要填写文本并确认，或将其排除。` : '保留片段均已确认，可以发布。'} 保存冲突时请先保留需要的文本，再重新读取草稿。</p>
      </> : <div className="empty"><strong>整理完成后，在这里校对每一句</strong><p>识别失败的片段仍可手工填写文本。</p></div>}
    </section>
    <section className="panel"><div className="section-top"><div><p className="eyebrow">03 / DATASET VERSIONS</p><h2>数据版本</h2></div><span className="pill">{versions.length} 个版本</span></div>{versions.length ? <div className="version-list"><PaginatedList items={versions ?? []} label="数据版本">{version => <article key={version.id} className="version-row"><div><strong>{version.role}</strong><p className="muted small">{version.id.slice(0, 8)} · {Array.isArray(version.clips) ? version.clips.length : version.clips} 段 · {seconds(version.seconds)}</p></div><div className="actions"><span className="pill">{version.feature_status?.toUpperCase() === 'READY' ? '特征已就绪' : '待准备训练特征'}</span><button disabled={busy || workRunning} onClick={() => void run(() => reviseVersion(version))}>修改标注</button></div></article>}</PaginatedList></div> : <p className="muted small">发布校对后的草稿，即可创建第一个数据版本。</p>}</section>
  </>;
}
