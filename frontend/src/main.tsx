import {PaginatedList} from './Pagination';
import {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {active, api, errorText, Job, JobEvent, kindLabel, requestId, stateLabel} from './api';
import {Datasets} from './Datasets';
import {Training, TrainingRun} from './Training';
import {TaskProgress} from './Progress';
import {Models} from './Models';
import {Synthesis} from './Synthesis';
import './style.css';

type Settings = {data_root: string; upstream: string; worker_python: string; port: number};
type Health = {name: string; service: string; version: string};
type Asset = {id?: string; name?: string; path?: string; status?: string; present?: boolean; exists?: boolean; valid?: boolean; error?: string};
type SystemStatus = {gpu?: string | Record<string, unknown>; gpu_jobs?: Job[]; gpu_resident_models?: {job_id: string; model_id: string; stage?: string}[]; active_jobs: number; assets?: Asset[]; worker_python?: string};

function AssetStatus({asset}: {asset: Asset}) {
  const status = asset.status?.toLowerCase();
  const passed = asset.valid === true || ['ready', 'verified', 'valid', 'ok'].includes(status ?? '');
  const missing = asset.present === false || asset.exists === false || ['missing', 'absent'].includes(status ?? '');
  const failed = asset.valid === false && !missing || ['invalid', 'corrupt', 'mismatch'].includes(status ?? '');
  return <div className="asset-row"><span title={asset.path}>{asset.name ?? asset.id ?? asset.path ?? '资产'}</span><strong className={passed ? 'verified' : 'needs-work'}>{passed ? '校验通过' : missing ? '缺失' : failed ? '校验失败' : asset.status ?? '待校验'}</strong>{asset.error && <small>{asset.error}</small>}</div>;
}

function Mark() {
  return <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true"><rect width="40" height="40" rx="12" fill="currentColor"/><g stroke="#f3f5df" strokeWidth="2.5" strokeLinecap="round"><path d="M10 18v4M15 12v16M20 8v24M25 14v12M30 18v4"/></g></svg>;
}

function App() {
  const [tab, setTab] = useState<'training' | 'tts' | 'management'>('training');
  const [requestedVersion, setRequestedVersion] = useState('');
  const [selectionToken,setSelectionToken] = useState(0);
  const [requestedModel, setRequestedModel] = useState('official');
  const [catalogVersion, setCatalogVersion] = useState(0);
  const [health, setHealth] = useState<Health>();
  const [settings, setSettings] = useState<Settings>();
  const [system, setSystem] = useState<SystemStatus>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState(() => {try {return localStorage.getItem('dictionforge.selectedJob') ?? '';} catch {return '';}});
  const [snapshot, setSnapshot] = useState<Job>();
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [processLogs, setProcessLogs] = useState<{name: string; text: string}[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState('连接中');
  const eventCursor = useRef(0);
  const refresh = useCallback(async () => {
    const [healthResult, settingsResult, jobsResult, systemResult] = await Promise.allSettled([api<Health>('/health'), api<Settings>('/settings'), api<Job[]>('/jobs'), api<SystemStatus>('/system/status')]);
    if (healthResult.status === 'fulfilled') setHealth(healthResult.value); else setHealth(undefined);
    if (settingsResult.status === 'fulfilled') setSettings(settingsResult.value);
    if (jobsResult.status === 'fulfilled') setJobs(jobsResult.value);
    if (systemResult.status === 'fulfilled') setSystem(systemResult.value);
  }, []);
  useEffect(() => {void refresh(); const timer = window.setInterval(() => void refresh(), 4000); return () => window.clearInterval(timer);}, [refresh]);
  useEffect(() => {try {localStorage.setItem('dictionforge.selectedJob', selected);} catch {/* The workbench also works when browser storage is unavailable. */}}, [selected]);
  useEffect(() => {
    if (!selected) {setSnapshot(undefined); return;}
    let disposed = false;
    setEvents([]); setProcessLogs([]); setSnapshot(undefined); eventCursor.current = 0; setConnection('连接中');
    const sync = () => api<Job>('/jobs/' + selected).then(job => {if (!disposed) setSnapshot(job);}).catch(e => {if (!disposed) setError(e.message);});
    void sync();
    const stream = new EventSource('/api/jobs/' + selected + '/events');
    stream.onopen = () => {setConnection('实时更新'); void sync();};
    stream.onmessage = message => {
      try {
        const event = JSON.parse(message.data) as JobEvent;
        const sequence = Number(message.lastEventId || event.event_seq || event.id || 0);
        if (sequence && sequence <= eventCursor.current) return;
        eventCursor.current = sequence;
        setEvents(current => [...current.slice(-99), event]);
        void sync(); void refresh();
      } catch {setError('任务事件格式无法读取，请刷新任务状态。');}
    };
    stream.addEventListener('end', () => {stream.close(); setConnection('状态已同步'); void sync(); void refresh();});
    stream.onerror = () => {setConnection('正在重新连接'); void sync();};
    return () => {disposed = true; stream.close();};
  }, [selected, refresh]);
  const diagnose = async () => {
    setBusy(true); setError('');
    try {const job = await api<Job>('/system/probe', requestId()); setSelected(job.id); await refresh();} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const prepareAssets = async () => {
    setBusy(true); setError('');
    try {const job = await api<Job>('/assets/prepare', requestId()); setSelected(job.id); await refresh();} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const submit = async (path: string, payload: object) => {
    const job = await api<Job>(path, {...requestId(), ...payload});
    setSelected(job.id); await refresh(); return job;
  };
  const cancel = async () => {
    if (!selected) return;
    setBusy(true); setError('');
    try {await api('/jobs/' + selected + '/control', {...requestId(), action: 'cancel'}); await refresh(); setSnapshot(await api<Job>('/jobs/' + selected));} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const retryModelExport = async (id: string) => {
    setBusy(true); setError('');
    try {await submit('/model-exports/' + encodeURIComponent(id) + '/retry', {});} catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const loadLogs = async () => {
    if (!selected) return;
    setLogsLoading(true);
    try {setProcessLogs(await api<{name: string; text: string}[]>('/jobs/' + selected + '/log'));} catch (e) {setError((e as Error).message);} finally {setLogsLoading(false);}
  };
  const listed = jobs.find(job => job.id === selected);
  const current = snapshot ? {...listed, ...snapshot, latest_progress: snapshot.latest_progress ?? listed?.latest_progress} : listed;
  const count = jobs.filter(active).length;
  return <main>
    <header className="masthead"><a className="brand" href="/" aria-label="声句工坊首页"><Mark/><div><h1>声句工坊</h1><span>DICTIONFORGE</span></div></a><div className="service"><i className={health ? 'online' : ''}/>{health ? '本地服务已连接' : '正在连接本地服务'}<span className="local-tag">本地工作台</span></div></header>
    <div className="intro"><div><p className="eyebrow">让每一句，更像你的声音</p><h2>从角色录音，到自然表达。</h2><p>整理录音、微调音色，再用文字生成语音。</p></div><div className="engine-badge"><span>GPT-SoVITS</span><strong>v4</strong><small>原生 48 kHz 语音合成</small></div></div>
    <nav aria-label="主工作区"><button aria-current={tab === 'training' ? 'page' : undefined} onClick={() => setTab('training')}><span>01</span>微调训练</button><button aria-current={tab === 'tts' ? 'page' : undefined} onClick={() => setTab('tts')}><span>02</span>语音合成</button><button aria-current={tab === 'management' ? 'page' : undefined} onClick={() => setTab('management')}><span>03</span>数据与模型管理</button><a href="#tasks">后台任务{count > 0 && <b>{count}</b>}</a></nav>
    {error && <div className="alert" role="alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError('')}>×</button></div>}
    <div className="workspace"><div className="primary">
      <div hidden={tab !== 'training'}><Datasets jobs={jobs} submit={submit} reportError={setError} onPublished={id => {setRequestedVersion(id); setRequestedModel('official'); setCatalogVersion(value => value + 1);}} refreshToken={catalogVersion}/><Training jobs={jobs} submit={submit} reportError={setError} requestedVersion={requestedVersion} requestedModel={requestedModel} selectionToken={selectionToken} refreshToken={catalogVersion}/></div>
      <div hidden={tab !== 'tts'}><Synthesis jobs={jobs} submit={submit} reportError={setError} refreshToken={catalogVersion} onCatalogChange={() => setCatalogVersion(value => value + 1)} system={system} onTrain={(version,model)=>{setSelectionToken(value=>value+1);setRequestedVersion(version);setRequestedModel(model);setCatalogVersion(value=>value+1);setTab('training');setTimeout(()=>document.getElementById('training-config')?.scrollIntoView({behavior:'smooth'}),0);}}/></div>
      <div hidden={tab !== 'management'}><Models jobs={jobs} submit={submit} reportError={setError} onCatalogChange={() => setCatalogVersion(value => value + 1)} showDatasets={true} refreshToken={catalogVersion}/></div><section className="panel" id="tasks"><div className="section-top"><div><p className="eyebrow">TASKS</p><h2>后台任务</h2></div><button className="quiet" onClick={() => void refresh()}>刷新状态</button></div><p className="muted">任务在后台运行，切换页面不会停止。</p>{jobs.length ? <div className="job-list"><PaginatedList items={jobs ?? []} label="后台任务" revealIndex={jobs.findIndex(job => job.id === selected)}>{job => <button key={job.id} className={'job-row ' + (selected === job.id ? 'selected' : '')} onClick={() => setSelected(job.id)}><span className="job-icon" aria-hidden="true">{active(job) ? '◌' : job.state === 'COMPLETED' ? '✓' : '·'}</span><span className="job-name"><strong>{kindLabel(job.kind)}</strong><small>{job.id.slice(0, 8)}{job.created ? ' · ' + new Date(job.created * 1000).toLocaleString('zh-CN', {timeZone: 'Asia/Hong_Kong'}) : ''}</small></span><span className={'state ' + job.state.toLowerCase()}>{stateLabel(job.state)}</span></button>}</PaginatedList></div> : <div className="empty"><span aria-hidden="true">◎</span><strong>还没有后台任务</strong><p>运行一次环境诊断，确认本地工作台已准备好。</p></div>}
      {current && <div className="task-detail"><div className="section-top"><h3>{kindLabel(current.kind)} · {stateLabel(current.state)}</h3><span className="muted small">{connection}</span></div><TaskProgress progress={current.latest_progress} training={current.kind === 'train'}/>{current.kind === 'train' && <TrainingRun job={current} submit={submit} jobs={jobs}/>}{current.error && <div className="alert" role="alert">{errorText(current.error)}{typeof current.error === 'object' && <small>阶段：{current.error.stage ?? '未指定'} · {current.error.retryable ? '可重试' : '请先处理错误条件'}</small>}</div>}{current.kind === 'model_export' && current.state === 'FAILED' && <button disabled={busy} onClick={() => void retryModelExport(current.id)}>从同一快照重试导出</button>}{active(current) && <button disabled={busy} onClick={cancel}>取消任务</button>}{current.result && <details open><summary>运行结果</summary><pre>{JSON.stringify(current.result, null, 2)}</pre></details>}<details><summary>任务日志 · {events.length} 条最近事件</summary><pre>{events.length ? events.map(event => JSON.stringify(event)).join('\n') : '等待事件记录'}</pre></details><details><summary>工作进程日志{current.error ? ' · 失败详情请在此查看' : ''}</summary><p className="muted small">保留每个日志文件最近 64 KiB，手动刷新获取最新内容。</p><button disabled={logsLoading} onClick={loadLogs}>{logsLoading ? '读取中…' : '刷新进程日志'}</button><PaginatedList items={processLogs ?? []} label="进程日志" key={selected}>{log => <div key={log.name}><h3>{log.name}</h3><pre>{log.text || '日志尚无内容'}</pre></div>}</PaginatedList></details><p className="muted small">任务编号：{current.id}</p></div>}</section>
    </div><aside>
      <section className="panel environment">
        <p className="eyebrow">WORKSPACE</p><h2>本机环境</h2>
        <div className="env-line"><span>服务状态</span><strong>{health ? '可用' : '未连接'}</strong></div>
        <div className="env-line"><span>活动任务</span><strong>{system?.active_jobs ?? count}</strong></div>
        <div className="env-line"><span>服务端口</span><strong>{settings?.port ?? '—'}</strong></div>
        {system?.gpu && <details><summary>GPU 资源状态</summary><pre>{typeof system.gpu === 'string' ? system.gpu : JSON.stringify(system.gpu, null, 2)}</pre></details>}
        <button className="wide" disabled={!health || busy} onClick={diagnose}>{busy ? '正在提交…' : '运行环境诊断'}</button>
        <details><summary>路径与设置</summary>{settings ? <dl><dt>应用数据</dt><dd>{settings.data_root}</dd><dt>上游源码</dt><dd>{settings.upstream}</dd><dt>模型运行环境</dt><dd>{system?.worker_python ?? settings.worker_python}</dd></dl> : <p className="muted">服务连接后显示设置。</p>}</details>
      </section>
      <section className="panel environment">
        <p className="eyebrow">MODEL ASSETS</p><h2>预训练资产</h2><p className="small">底模、声码器、文本与语音识别资源。</p>
        {system?.assets?.length ? <details open><summary>资产校验 · {system.assets.length} 项</summary><div className="asset-list"><PaginatedList items={system.assets ?? []} label="预训练资产">{(asset, index) => <AssetStatus key={asset.id ?? asset.path ?? index} asset={asset}/>}</PaginatedList></div></details> : <p className="muted small">等待读取资产清单。</p>}
        <button className="wide" disabled={!health || busy} onClick={prepareAssets}>准备预训练资产</button><p className="muted small">下载进度和错误可在后台任务查看。</p>
      </section>
      <div className="aside-note"><span aria-hidden="true">✳</span><p>声音的每一次进步，<br/>都从一句好素材开始。</p></div>
    </aside></div>
    <footer><span>声句工坊 · DictionForge</span><span>本地运行 · {health?.version ?? '0.1.0'}</span></footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<App/>);
