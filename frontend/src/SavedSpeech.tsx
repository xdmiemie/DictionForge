import {PaginatedList, Pagination, PAGE_SIZE} from './Pagination';
import {useEffect, useRef, useState} from 'react';
import {api, Job} from './api';
import {LeasedAudio} from './MediaLease';
import type {ModelVersion} from './Models';

export type SavedRecord = {id: string; title: string; text: string; model_id: string; model_name?: string; format: string; created: number; audio: {audio_id: string; seconds: number; sample_rate: number; sha256: string; bytes: number}};
type Page = {items: SavedRecord[]; total: number; page: number; page_size: number; pages: number};
export type SpeechDeletePreview = {token: string; preview_id: string; ids: string[]; count: number; items: {id: string; title: string; files: unknown[]}[]; filter?: {model_id?: string; query?: string}; expires: number};
export function SavedDeleteConfirmation({preview, scope, busy, cancel, confirm}: {preview: SpeechDeletePreview; scope: string; busy: boolean; cancel: () => void; confirm: () => void}) {
  return <div className="delete-preview" role="region" aria-label="作品删除确认"><div className="section-top"><h3>确认删除 {preview.count} 件作品</h3><button disabled={busy} onClick={cancel}>取消删除</button></div><p>范围：{scope}。只删除下列确认集合；确认后新增的作品不包含在内。</p>
    {preview.filter && <p className="small">筛选模型：{preview.filter.model_id || '全部'} · 关键词：{preview.filter.query || '无'}</p>}
    <div className="delete-files"><PaginatedList items={preview.items ?? []} label="待删除作品" key={preview.preview_id ?? preview.token}>{item => <div key={item.id} data-preview-id={item.id}><strong>{item.title || '未命名作品'}</strong><p>{item.id}</p><details><summary>包含的文件</summary><pre>{JSON.stringify(item.files, null, 2)}</pre></details></div>}</PaginatedList></div>
    <p className="muted small">正在播放、下载或被任务引用的作品会保留，并在结果中说明原因。</p><button className="danger" disabled={busy || !preview.count} onClick={confirm}>确认删除这 {preview.count} 件作品</button></div>;
}

export function SavedSpeech({jobs, models}: {jobs: Job[]; models: ModelVersion[]}) {
  const [page, setPage] = useState(1), [modelId, setModelId] = useState(''), [query, setQuery] = useState('');
  const [data, setData] = useState<Page>(), [selected, setSelected] = useState<string[]>([]), [reload, setReload] = useState(0);
  const [preview, setPreview] = useState<{value: SpeechDeletePreview; scope: string}>(), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [deletion, setDeletion] = useState<{results: {id: string; status: string; reason?: string}[]; counts: {deleted: number; already_deleted: number; protected: number}}>();
  const sequence = useRef(0);
  const jobsKey = jobs.filter(job => job.kind === 'speech_save').map(job => job.id + ':' + job.state).join('|');
  useEffect(() => {
    const ticket = ++sequence.current;
    setData(undefined); setSelected([]); setError('');
    const params = new URLSearchParams({model_id: modelId, query, page: String(page), page_size: String(PAGE_SIZE)});
    void api<Page>('/saved-speech?' + params).then(value => {if (ticket === sequence.current) {if (page > Math.max(1, value.pages)) setPage(Math.max(1, value.pages)); else setData(value);}}).catch(reason => {if (ticket === sequence.current) setError(reason.message);});
    return () => {sequence.current++;};
  }, [page, modelId, query, reload, jobsKey]);
  const perform = async (operation: () => Promise<void>) => {setBusy(true); setError(''); try {await operation();} catch (reason) {setError((reason as Error).message);} finally {setBusy(false);}};
  const requestPreview = async (ids?: string[]) => {const value = await api<SpeechDeletePreview>('/saved-speech/delete-preview', ids ? {ids} : {all_filtered: true, model_id: modelId, query}); setPreview({value, scope: ids ? ids.length === 1 ? '指定单件作品' : '本页所选作品' : '当前筛选的全部结果（包含其他页）'});};
  const remove = async () => {if (!preview) return; const result = await api<NonNullable<typeof deletion>>('/saved-speech/delete', {preview_id: preview.value.preview_id ?? preview.value.token}); setDeletion(result); setNotice(`已删除 ${result.counts.deleted} 件，先前已删除 ${result.counts.already_deleted} 件，受保护 ${result.counts.protected} 件。`); setPreview(undefined); setSelected([]); setReload(value => value + 1);};
  const changeFilter = (fn: () => void) => {setSelected([]); setPreview(undefined); setPage(1); fn();};
  return <section className="saved-speech" aria-label="已保存语音"><div className="section-top"><div><p className="eyebrow">SAVED SPEECH</p><h3>已保存语音</h3></div><button className="quiet" disabled={busy} onClick={() => setReload(value => value + 1)}>刷新作品库</button></div>
    <p className="muted small">独立保存的作品可跨重启播放。按创建时间倒序显示，每页最多 5 件。</p>
    <div className="grid"><label>筛选来源模型<select aria-label="作品模型筛选" value={modelId} disabled={busy} onChange={event => changeFilter(() => setModelId(event.target.value))}><option value="">全部模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.role} · {model.id.slice(0, 8)}</option>)}</select></label><label>筛选标题或正文<input aria-label="作品关键词筛选" value={query} disabled={busy} placeholder="输入标题或朗读文本" onChange={event => changeFilter(() => setQuery(event.target.value))}/></label></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="success-note" role="status">{notice}</p>}
    {deletion && <details><summary>逐项删除结果</summary><PaginatedList items={deletion.results ?? []} label="作品删除结果">{item => <p className="small path-text" key={item.id}>{item.id} · {{deleted: '已删除', already_deleted: '已删除过', protected: '受引用保护'}[item.status] ?? item.status}{item.reason && ' · ' + item.reason}</p>}</PaginatedList></details>}
    <div className="actions"><button disabled={busy || !data?.items.length} onClick={() => setSelected(selected.length === data?.items.length ? [] : data?.items.map(item => item.id) ?? [])}>{data?.items.length && selected.length === data.items.length ? '取消本页全选' : '全选本页'}</button><button className="danger" disabled={busy || !selected.length} onClick={() => void perform(() => requestPreview(selected))}>预览删除本页所选（{selected.length}）</button><button className="danger" disabled={busy || !data?.total} onClick={() => void perform(() => requestPreview())}>预览删除当前筛选全部（{data?.total ?? 0}）</button></div>
    {preview && <SavedDeleteConfirmation preview={preview.value} scope={preview.scope} busy={busy} cancel={() => setPreview(undefined)} confirm={() => void perform(remove)}/>}
    {!data ? <p className="muted small">正在读取作品…</p> : <><div className="saved-speech-list">{data.items.map(item => <article className="saved-speech-card" data-saved-id={item.id} key={item.id}><div className="section-top"><label className="saved-select"><input type="checkbox" aria-label={'选择作品 ' + item.title} checked={selected.includes(item.id)} disabled={busy} onChange={event => setSelected(values => event.target.checked ? [...values, item.id] : values.filter(id => id !== item.id))}/><strong>{item.title || '未命名作品'}</strong></label><span className="pill">{item.format.toUpperCase()}</span></div><p className="reference-transcript">{item.text}</p><p className="muted small path-text">{item.model_name || item.model_id} · {new Date(item.created * 1000).toLocaleString('zh-CN')} · {item.audio.seconds.toFixed(2)} 秒 · {item.audio.sample_rate / 1000} kHz</p><LeasedAudio audioId={item.audio.audio_id} label={'作品音频 ' + item.title}/><div className="actions"><a className="download-link" href={'/api/audio/' + encodeURIComponent(item.audio.audio_id) + '?download=true'} download>下载 {item.format.toUpperCase()}</a><button className="quiet danger" disabled={busy} onClick={() => void perform(() => requestPreview([item.id]))}>删除此作品</button></div><details><summary>文件与来源编号</summary><pre>{JSON.stringify({id: item.id, model_id: item.model_id, audio: item.audio}, null, 2)}</pre></details></article>)}</div>{!data.items.length && <p className="muted">没有匹配的作品。可从合成结果保存 WAV 或 MP3。</p>}<Pagination label="已保存语音" page={page} pages={data.pages} total={data.total} disabled={busy} onPage={value => {setSelected([]); setPreview(undefined); setPage(value);}}/></>}
  </section>;
}
