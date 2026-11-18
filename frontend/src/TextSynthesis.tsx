import {PaginatedList} from './Pagination';
import {useCallback, useEffect, useRef, useState} from 'react';
import {active, api, errorText, finiteNumber, Job, JobError, stateLabel, requestId} from './api';
import {isGone, useMediaLease} from './MediaLease';
import {AppendSentence, type TrainSelection} from './AppendSentence';
import type {ReferenceAudio} from './Synthesis';

export type SpeechAudio = {audio_id?: string; sample_rate: number; samples?: number; seconds?: number; sha256?: string; state?: string; available?: boolean};
export type SpeechSentence = {sentence_id: string; index: number; text: string; source_start: number; source_end: number; seed?: number; actual_seed?: number; start_sample: number; end_sample: number; audio: SpeechAudio; leading_silence_samples?: number; trailing_silence_samples?: number};
type Parameters = {seed: number; sample_steps: number; top_k: number; top_p: number; temperature: number; speed_factor: number; repetition_penalty: number; fragment_interval: number};
type FrozenRequest = {text: string; language: string; model_id?: string; model_version_id?: string; split_rule?: string; reference?: ReferenceAudio; parameters?: Parameters; sentences?: {sentence_id: string; index: number; text: string; source_start: number; source_end: number}[]};
type SpeechFailure = {phase?: string; stage?: string; sentence_id?: string; sentence_index?: number; index?: number; text?: string; error?: string; [key: string]: unknown};
export type SpeechResult = {audio?: SpeechAudio; sentences?: SpeechSentence[]; text?: string; error?: string | JobError; parameters?: Parameters; metrics?: Record<string, unknown>; phase?: string; sentence_id?: string; sentence_index?: number; sentence_text?: string; sentence?: SpeechFailure; actual_seed?: number; completed_sentences?: number; total_sentences?: number; normalized_texts?: string[]; failure?: SpeechFailure};
export type SpeechSnapshot = {id: string; job: Job; request: FrozenRequest; result?: SpeechResult | null};
type Submit = (path: string, payload: object) => Promise<Job>;
const defaults: Parameters = {seed: 42, sample_steps: 32, top_k: 5, top_p: 1, temperature: 1, speed_factor: 1, repetition_penalty: 1.35, fragment_interval: .3};
const audioUrl = (id: string) => '/api/audio/' + encodeURIComponent(id);
const phaseNames: Record<string, string> = {dependency_validation: '依赖校验', reference_validation: '参考校验', sentence_started: '开始生成句子', sentence_audio_writing: '写入句子音频', sentence_completed: '句子生成完成', audio_written: '音频写入完成', model_ready: '模型已加载', request_validation: '请求校验', model_loading: '加载模型', model_loaded: '模型加载完成', reference_processing: '参考音频处理', text_frontend: '文本前处理', synthesizing: '逐句生成', sentence_generation: '逐句生成', audio_writing: '写入音频', assembling: '拼接音频', completed: '生成完成', gpu_released: '释放 GPU'};

export function SpeechFailurePanel({result, error}: {result?: SpeechResult | null; error?: string | JobError}) {
  const index = result?.sentence_index ?? result?.sentence?.index;
  const sentenceText = result?.sentence_text ?? result?.sentence?.text;
  return <div className="speech-failure" role="alert"><p className="inline-error">合成失败：{errorText(error ?? result?.error)}</p>
    <p className="small">失败阶段：{result?.phase ? phaseNames[result.phase] ?? result.phase : typeof error === 'object' && error.stage ? error.stage : '未报告'}{finiteNumber(index) ? ` · 第 ${index + 1} 句` : ''}</p>
    {sentenceText && <p className="small reference-transcript">失败句文本：{sentenceText}</p>}
    <details><summary>失败条件与定位记录</summary><pre>{JSON.stringify({phase: result?.phase, sentence_id: result?.sentence_id, sentence_index: index, sentence_text: sentenceText, actual_seed: result?.actual_seed, completed_sentences: result?.completed_sentences, total_sentences: result?.total_sentences, sentence: result?.sentence, failure: result?.failure, normalized_texts: result?.normalized_texts, parameters: result?.parameters}, null, 2)}</pre></details>
  </div>;
}

// API offsets count Unicode code points. JavaScript string.slice counts UTF-16
// code units, so every visible source range must pass through this conversion.
export const codepointSlice = (text: string, start: number, end?: number) => Array.from(text).slice(start, end).join('');
export function sentenceAtTime(sentences: SpeechSentence[], seconds: number, sampleRate: number): SpeechSentence | undefined {
  if (!finiteNumber(seconds) || !finiteNumber(sampleRate) || sampleRate <= 0 || seconds < 0) return undefined;
  const sample = Math.floor(seconds * sampleRate + 1e-7);
  const match = sentences.find(sentence => sample >= sentence.start_sample && sample < sentence.end_sample);
  if (match) return match;
  const last = sentences.at(-1);
  return last && sample === last.end_sample ? last : undefined;
}

export function FrozenText({text, sentence}: {text: string; sentence?: SpeechSentence}) {
  const validRange = sentence && Number.isInteger(sentence.source_start) && Number.isInteger(sentence.source_end) && sentence.source_start >= 0 && sentence.source_end >= sentence.source_start;
  return <div className="frozen-text" aria-label="冻结朗读文本">{validRange ? <>{codepointSlice(text, 0, sentence.source_start)}<mark>{codepointSlice(text, sentence.source_start, sentence.source_end)}</mark>{codepointSlice(text, sentence.source_end)}</> : text}</div>;
}

export function SpeechPlayback({snapshot, historical = false, submit, onTrain}: {snapshot: SpeechSnapshot; historical?: boolean; submit?: Submit; onTrain?: TrainSelection}) {
  const result = snapshot.result ?? snapshot.job.result as SpeechResult | undefined;
  const audio = result?.audio;
  const sentences = result?.sentences ?? [];
  const [selectedId, setSelectedId] = useState('');
  const [playError, setPlayError] = useState('');
  const [playerOpen, setPlayerOpen] = useState(true);
  const [title, setTitle] = useState(''), [format, setFormat] = useState<'wav' | 'mp3'>('wav');
  const [saving, setSaving] = useState(false), [saveNotice, setSaveNotice] = useState(''), [saveGone, setSaveGone] = useState(false);
  const unavailable = result && ['DELETED', 'PURGE_PENDING'].includes(String((result as SpeechResult & {media_state?: string}).media_state)) || audio?.available === false || ['DELETED', 'PURGE_PENDING'].includes(audio?.state ?? '');
  const lease = useMediaLease(audio?.audio_id, playerOpen && snapshot.job.state === 'COMPLETED' && !unavailable && !saveGone);
  const gone = unavailable || lease.gone || saveGone;
  const save = async () => {
    if (!submit || !lease.ready || gone) return;
    setSaving(true); setPlayError(''); setSaveNotice('');
    try {const job = await submit('/tts/' + encodeURIComponent(snapshot.id) + '/save', {title, format}); setSaveNotice(`保存任务已受理（${job.id.slice(0, 8)}），完成后可在下方作品库播放与下载。`);} catch (error) {if (isGone(error)) setSaveGone(true); setPlayError((error as Error).message);} finally {setSaving(false);}
  };
  const mainAudio = useRef<HTMLAudioElement>(null);
  const sentenceAudios = useRef<Record<string, HTMLAudioElement | null>>({});
  const playbackMode = useRef<'main' | 'sentence' | 'manual'>('manual');
  const pendingSeek = useRef<{id: string; seconds: number} | null>(null);
  const selected = sentences.find(sentence => sentence.sentence_id === selectedId);
  const sentenceKey = sentences.map(sentence => sentence.sentence_id).join('|');
  useEffect(() => {setSelectedId(sentences[0]?.sentence_id ?? ''); setPlayError(''); pendingSeek.current = null; playbackMode.current = 'manual';}, [snapshot.id, sentenceKey]);
  const seek = (sentence: SpeechSentence) => {
    setSelectedId(sentence.sentence_id); setPlayError('');
    pauseSentences();
    if (!mainAudio.current || !audio || !finiteNumber(audio.sample_rate) || audio.sample_rate <= 0) return;
    const seconds = sentence.start_sample / audio.sample_rate;
    playbackMode.current = mainAudio.current.paused ? 'manual' : 'main';
    pendingSeek.current = {id: sentence.sentence_id, seconds};
    try {mainAudio.current.currentTime = seconds;} catch {pendingSeek.current = null; setPlayError('完整音频尚未加载，请先播放或等待元数据载入后再定位。');}
  };
  const pauseSentences = (except?: string) => {for (const [id, player] of Object.entries(sentenceAudios.current)) if (id !== except) player?.pause();};
  return <div className="speech-playback" data-result-id={snapshot.id}><div className="section-top"><h3>{historical ? '历史任务合成结果' : '当前合成结果'}</h3><span className="pill">{stateLabel(snapshot.job.state)}</span></div>
    <p className="muted small">结果使用受理时冻结的文本、模型和参考。编辑上方输入不会改变这份结果。</p>
    <p className="muted small path-text">任务 {snapshot.id} · 来源模型 {snapshot.request.model_version_id ?? snapshot.request.model_id ?? '未报告'}</p>
    <FrozenText text={snapshot.request.text} sentence={selected}/>
    {(snapshot.job.error || result?.error) && <SpeechFailurePanel result={result} error={snapshot.job.error}/>}
    {snapshot.job.state === 'COMPLETED' && audio?.audio_id ? <>
      {gone ? <p className="notice" role="status">这份临时音频已回收，无法播放或保存。冻结文本与任务记录仍可查看；已保存的作品请在作品库读取。</p> : <div className="actions"><button className="quiet" onClick={() => setPlayerOpen(value => !value)}>{playerOpen ? '关闭当前播放器' : '打开当前播放器'}</button>{lease.ready && <a className="download-link" href={audioUrl(audio.audio_id) + '?download=true'} download>下载当前 WAV</a>}</div>}
      {!gone && playerOpen && !lease.ready && <p className={lease.error ? 'inline-error' : 'muted small'}>{lease.error || '正在获取音频引用…'}</p>}
      {!gone && lease.ready && <>
      <audio ref={mainAudio} key={snapshot.id} className="complete-speech-audio" aria-label="完整合成音频" controls preload="metadata" src={audioUrl(audio.audio_id)}
        onPlay={() => {playbackMode.current = 'main'; pauseSentences();}} onError={() => setPlayError('音频加载失败，请刷新结果并检查后台日志。')}
        onTimeUpdate={event => {if (playbackMode.current !== 'main' || pendingSeek.current) return; const sentence = sentenceAtTime(sentences, event.currentTarget.currentTime, audio.sample_rate); if (sentence) setSelectedId(sentence.sentence_id);}}
        onSeeked={event => {if (playbackMode.current === 'sentence') return; const target = pendingSeek.current; if (target) {if (Math.abs(event.currentTarget.currentTime - target.seconds) < .05) {setSelectedId(target.id); pendingSeek.current = null;} return;} const sentence = sentenceAtTime(sentences, event.currentTarget.currentTime, audio.sample_rate); if (sentence) setSelectedId(sentence.sentence_id);}}/>
      <p className="muted small">{finiteNumber(audio.sample_rate) ? `${audio.sample_rate / 1000} kHz` : '采样率未报告'}{finiteNumber(audio.seconds) ? ` · ${audio.seconds.toFixed(2)} 秒` : ''} · {sentences.length} 句{selected ? ` · 当前第 ${selected.index + 1} 句` : ''}</p>
      {playError && <p className="inline-error">{playError}</p>}
      <div className="speech-sentences" aria-label="逐句合成结果"><PaginatedList items={sentences ?? []} label="合成句子" key={snapshot.id} revealIndex={sentences.findIndex(sentence => sentence.sentence_id === selectedId)}>{sentence => <article className={'speech-sentence ' + (sentence.sentence_id === selectedId ? 'is-current' : '')} key={sentence.sentence_id} data-sentence-id={sentence.sentence_id} aria-current={sentence.sentence_id === selectedId ? 'true' : undefined}>
        <button className="sentence-select" aria-label={`选择第 ${sentence.index + 1} 句并定位`} aria-pressed={sentence.sentence_id === selectedId} onClick={() => seek(sentence)}><span>{String(sentence.index + 1).padStart(2, '0')}</span><strong>{sentence.text}</strong><small>在完整音频中定位</small></button>
        {sentence.audio?.audio_id && <audio controls preload="none" aria-label={`第 ${sentence.index + 1} 句音频`} ref={element => {sentenceAudios.current[sentence.sentence_id] = element;}} src={audioUrl(sentence.audio.audio_id)} onPlay={() => {playbackMode.current = 'sentence'; pendingSeek.current = null; mainAudio.current?.pause(); pauseSentences(sentence.sentence_id); setSelectedId(sentence.sentence_id);}}/>}
        <p className="muted small">{finiteNumber(audio.sample_rate) && audio.sample_rate > 0 ? `${(sentence.start_sample / audio.sample_rate).toFixed(2)}–${(sentence.end_sample / audio.sample_rate).toFixed(2)} 秒` : '时间区间未报告'} · 实际种子 {sentence.actual_seed ?? '未报告'}</p>
        <details><summary>句子定位与实际记录</summary><pre>{JSON.stringify({sentence_id: sentence.sentence_id, source_start: sentence.source_start, source_end: sentence.source_end, seed: sentence.seed, actual_seed: sentence.actual_seed, start_sample: sentence.start_sample, end_sample: sentence.end_sample, audio: sentence.audio}, null, 2)}</pre></details>
      </article>}</PaginatedList></div>
      </>}
      {submit && !gone && <div className="speech-save"><h3>保存独立作品</h3><div className="grid"><label>作品标题<input aria-label="作品标题" value={title} maxLength={200} placeholder="可选，留空使用朗读文本" onChange={event => setTitle(event.target.value)}/></label><label>保存格式<select aria-label="保存格式" value={format} onChange={event => setFormat(event.target.value as 'wav' | 'mp3')}><option value="wav">WAV · 原生 48 kHz 无损</option><option value="mp3">MP3 · 320 kbps</option></select></label></div><button className="primary-button" disabled={saving || !lease.ready} onClick={() => void save()}>{saving ? '正在受理保存…' : '保存到作品库'}</button>{!lease.ready && <p className="muted small">打开播放器并确认音频可用后保存。</p>}{saveNotice && <p role="status" className="success-note">{saveNotice}</p>}</div>}
    </> : <p className="muted small">{active(snapshot.job) ? '合成任务在后台排队或生成，完成后会显示完整音频和每句结果。' : '当前任务尚无成功的完整音频。'}</p>}
    {submit && snapshot.job.state === 'COMPLETED' && <AppendSentence ttsId={snapshot.id} sentence={selected} available={lease.ready&&!gone} submit={submit} onTrain={onTrain}/>}
    <details><summary>冻结参考与生成条件</summary><pre>{JSON.stringify({model_id: snapshot.request.model_version_id ?? snapshot.request.model_id, language: snapshot.request.language, split_rule: snapshot.request.split_rule, reference: snapshot.request.reference, parameters: snapshot.request.parameters, metrics: result?.metrics}, null, 2)}</pre></details>
  </div>;
}

export function TextSynthesis({modelId, canGenerate, reference, jobs, submit, reportError, onTrain}: {modelId: string; canGenerate: boolean; reference?: ReferenceAudio | null; jobs: Job[]; submit: Submit; reportError: (message: string) => void; onTrain?: TrainSelection}) {
  const [text, setText] = useState(() => {try {return localStorage.getItem('dictionforge.textDraft') ?? '';} catch {return '';}});
  const [splitRule, setSplitRule] = useState<'punctuation' | 'lines' | 'none'>('punctuation');
  const [parameters, setParameters] = useState<Parameters>(defaults);
  const [current, setCurrent] = useState<SpeechSnapshot | null>();
  const [historyId, setHistoryId] = useState('');
  const [history, setHistory] = useState<SpeechSnapshot>();
  const [historyError, setHistoryError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  const currentRequest = useRef(0);
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');
  const refresh = useCallback(async (id?: string) => {const ticket = ++currentRequest.current; const value = await api<SpeechSnapshot | null>(id ? '/tts/' + encodeURIComponent(id) : '/tts/current'); if (ticket === currentRequest.current) {setCurrent(value); setLoadError('');}}, []);
  useEffect(() => {void refresh().catch(error => setLoadError(error.message));}, [refresh, jobsKey]);
  useEffect(() => {
    const deleted = (event: Event) => {
      const ids = (event as CustomEvent<{deleted_jobs?:string[]}>).detail.deleted_jobs ?? [];
      if (ids.includes(historyId)) {setHistoryId('');setHistory(undefined);setHistoryError('');}
      void refresh().catch(error => setLoadError(error.message));
    };
    window.addEventListener('dictionforge:catalog-deleted', deleted);
    return () => window.removeEventListener('dictionforge:catalog-deleted', deleted);
  }, [historyId, refresh]);
  useEffect(() => {try {localStorage.setItem('dictionforge.textDraft', text);} catch {/* The input remains usable without storage. */}}, [text]);
  useEffect(() => {
    if (!historyId) return;
    let disposed = false; let timer: ReturnType<typeof window.setTimeout> | undefined;
    setHistory(undefined); setHistoryError('');
    const read = () => api<SpeechSnapshot>('/tts/' + encodeURIComponent(historyId)).then(value => {if (!disposed) {setHistory(value); if (active(value.job)) timer = window.setTimeout(() => void read(), 2000);}}).catch(error => {if (!disposed) setHistoryError(error.message);});
    void read(); return () => {disposed = true; if (timer) window.clearTimeout(timer);};
  }, [historyId]);
  useEffect(() => {
    if (!current?.job || !active(current.job)) return;
    const timer = window.setInterval(() => void refresh().catch(error => setLoadError(error.message)), 2000);
    return () => window.clearInterval(timer);
  }, [current?.id, current?.job.state, refresh]);
  const change = <K extends keyof Parameters>(key: K, value: Parameters[K]) => setParameters(current => ({...current, [key]: value}));
  const generate = async () => {
    setBusy(true); reportError(''); setFormError(''); setNotice('');
    try {
      if (!text.trim()) throw new Error('请输入要朗读的中文文本。');
      if (!canGenerate) throw new Error('请先启用有效模型并确认默认参考。');
      const job = await submit('/tts', {model_version_id: modelId, text, language: 'zh', split_rule: splitRule, parameters});
      setNotice('合成请求已受理，文本与参考条件已经冻结。');
      setHistoryId(''); await refresh(job.id);
    } catch (error) {setFormError((error as Error).message); reportError((error as Error).message);} finally {setBusy(false);}
  };
  const cancel = async () => {if (!current || !active(current.job)) return; setBusy(true); try {await api('/jobs/' + encodeURIComponent(current.id) + '/control', {...requestId(), action: 'cancel'}); setNotice('取消请求已受理，等待后台任务退出。'); await refresh();} catch (error) {setFormError((error as Error).message);} finally {setBusy(false);}};
  return <section className="text-synthesis" aria-label="中文文本合成"><div className="section-top"><div><p className="eyebrow">TEXT TO SPEECH</p><h3>用文字生成语音</h3></div><span className="pill">当前支持中文</span></div>
    <label>待朗读文本<textarea aria-label="待朗读文本" className="speech-input" value={text} placeholder="输入要朗读的中文，可以包含多句或分行。" onChange={event => setText(event.target.value)}/></label>
    <div className="grid"><label>目标语言<select aria-label="目标语言" value="zh" disabled><option value="zh">中文 · 普通话（已实测）</option></select></label><label>语速倍率<input aria-label="语速倍率" type="number" min="0.5" max="2" step="0.05" value={parameters.speed_factor} onChange={event => change('speed_factor', event.target.valueAsNumber)}/></label></div>
    <details className="synthesis-advanced"><summary>断句与采样参数</summary><div className="grid"><label>断句方式<select aria-label="断句方式" value={splitRule} onChange={event => setSplitRule(event.target.value as typeof splitRule)}><option value="punctuation">按句末标点断句</option><option value="lines">按换行分句</option><option value="none">全文作为一句</option></select></label><label>基础随机种子<input aria-label="基础随机种子" type="number" min="0" max="4294967295" step="1" value={parameters.seed} onChange={event => change('seed', event.target.valueAsNumber)}/></label><label>采样步数<select aria-label="采样步数" value={parameters.sample_steps} onChange={event => change('sample_steps', Number(event.target.value))}>{[8, 16, 32].map(value => <option key={value} value={value}>{value}</option>)}</select></label><label>Top K<input aria-label="Top K" type="number" min="1" max="100" step="1" value={parameters.top_k} onChange={event => change('top_k', event.target.valueAsNumber)}/></label><label>Top P<input aria-label="Top P" type="number" min="0.01" max="1" step="0.01" value={parameters.top_p} onChange={event => change('top_p', event.target.valueAsNumber)}/></label><label>Temperature<input aria-label="Temperature" type="number" min="0.1" max="2" step="0.05" value={parameters.temperature} onChange={event => change('temperature', event.target.valueAsNumber)}/></label><label>重复惩罚<input aria-label="重复惩罚" type="number" min="1" max="2" step="0.05" value={parameters.repetition_penalty} onChange={event => change('repetition_penalty', event.target.valueAsNumber)}/></label><label>上游片段间隔（秒）<input aria-label="上游片段间隔" type="number" min="0.01" max="1" step="0.01" value={parameters.fragment_interval} onChange={event => change('fragment_interval', event.target.valueAsNumber)}/></label></div><p className="muted small">每句种子按基础种子与句序派生，实际值随结果记录。当前参数在请求受理时冻结。</p></details>
    <p className="muted small">{reference ? `当前参考：${reference.text}` : '请先设置默认参考并启用模型。'}</p>
    <div className="actions"><button className="primary-button" disabled={busy || !canGenerate || Boolean(current?.job && active(current.job))} onClick={() => void generate()}>{busy ? '正在提交…' : '生成语音'}</button><button className="quiet" disabled={busy} onClick={() => {setHistoryId(''); void refresh().catch(error => setLoadError(error.message));}}>{historyId ? '返回当前合成结果' : '刷新当前合成结果'}</button></div>
    {notice && <p className="success-note" role="status">{notice}</p>}{formError && <p className="inline-error" role="alert">{formError}</p>}{loadError && <p className="inline-error">当前结果读取失败：{loadError}</p>}
    {current && active(current.job) && <button className="quiet danger" disabled={busy} onClick={() => void cancel()}>取消当前合成任务</button>}
    <label>查看合成任务<select aria-label="查看合成任务" value={historyId} onChange={event => setHistoryId(event.target.value)}><option value="">当前合成结果</option>{jobs.filter(job => job.kind === 'tts' && job.id !== current?.id).map(job => <option key={job.id} value={job.id}>{job.id.slice(0, 8)} · {stateLabel(job.state)}{job.created ? ` · ${new Date(job.created * 1000).toLocaleString('zh-CN')}` : ''}</option>)}</select></label>
    {historyError && <p className="inline-error">历史结果读取失败：{historyError}</p>}
    {historyId ? history ? <SpeechPlayback key={history.id} snapshot={history} historical submit={submit} onTrain={onTrain}/> : <p className="muted small">正在读取历史合成任务…</p> : current ? <SpeechPlayback key={current.id} snapshot={current} submit={submit} onTrain={onTrain}/> : <p className="muted small">{current === null ? '还没有当前合成结果。生成后可播放完整语音或选择其中一句。' : '正在读取当前合成结果…'}</p>}
  </section>;
}
