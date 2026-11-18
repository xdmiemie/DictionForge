import {PaginatedList} from './Pagination';
import {useEffect, useRef, useState} from 'react';
import {active, api, finiteNumber, Job, stateLabel} from './api';
import type {DatasetVersion} from './Datasets';

type Weight = {path?: string; sha256: string; bytes: number};
export type ModelVersion = {id: string; model_id: string; version_id: string; role: string; status: string; weights: {gpt: Weight; sovits: Weight}; stage_progress?: Record<string, {state?: string; optimizer_updates?: number}>; validation?: {structure?: string; gpu?: string}; provenance?: {run_id?: string; version_id?: string; initial_model?: string; reason?: string; snapshot_id?: string}; dependencies?: unknown[]; default_reference?: unknown};
type DeletePreview = {preview_id?: string; token?: string; id?: string; files: {path: string; bytes?: number; sha256?: string}[]; references: {kind: string; id: string; reason: string}[]; deletable?: boolean; total_bytes?: number};
type Selection = {kind: 'model' | 'dataset' | 'draft'; id: string; title: string; preview: DeletePreview};
type PreviewTarget = Omit<Selection, 'preview'>;
type Submit = (path: string, payload: object) => Promise<Job>;
const kindName = (kind: Selection['kind']) => ({model:'模型版本',dataset:'数据版本',draft:'标注草稿'}[kind]);
const endpointFor = (kind: Selection['kind']) => ({model:'/models/',dataset:'/dataset-versions/',draft:'/dataset-drafts/'}[kind]);
const verification = (value?: string) => ({PASS: '通过', PASSED: '通过', NOT_RUN: '尚未运行', FAILED: '失败', FAIL: '失败'}[value ?? ''] ?? value ?? '尚未记录');
const size = (bytes?: number) => finiteNumber(bytes) ? `${(bytes / 2 ** 20).toFixed(1)} MiB` : '未记录大小';

export function ModelCard({model, busy, onPreview}: {model: ModelVersion; busy: boolean; onPreview: () => void}) {
  const initial = model.provenance?.initial_model === 'official' ? '官方底模' : model.provenance?.initial_model;
  return <article className="model-card"><div className="section-top"><h3>{model.role}</h3><span className="pill">{model.status === 'READY' ? '已注册' : model.status}</span></div>
    <p className="muted small path-text">版本 {model.version_id} · 模型 {model.model_id}</p>
    <div className="validation-grid"><span>结构检查 <strong>{verification(model.validation?.structure)}</strong></span><span>GPU 试听 <strong>{verification(model.validation?.gpu)}</strong></span></div>
    <p className="muted small">{model.id === 'official' ? '底模来源' : model.provenance?.reason === 'import' ? '导入权重来源' : '导出时阶段进度（冻结快照）'}</p>
    <div className="model-stages">{(['s2', 's1'] as const).map(stage => {const value = model.stage_progress?.[stage]; const initialized = value?.state === 'PENDING' || value?.state === 'SKIPPED'; return <p key={stage}>{stage === 's2' ? 'SoVITS v4' : 'GPT'} · {value?.state ? stateLabel(value.state) : '进度未记录'}{finiteNumber(value?.optimizer_updates) ? ` · 更新 ${value.optimizer_updates}` : ''}{initialized ? ` · 使用初始化权重${initial ? `（${initial}）` : ''}` : ''}</p>;})}</div>
    <details><summary>权重、依赖与来源</summary><dl>{(['gpt', 'sovits'] as const).map(name => <div key={name}><dt>{name === 'gpt' ? 'GPT' : 'SoVITS'} · {size(model.weights?.[name]?.bytes)}</dt><dd className="path-text">{model.weights?.[name]?.sha256 ?? '缺少哈希'}</dd></div>)}</dl><pre>{JSON.stringify({provenance: model.provenance, dependencies: model.dependencies, default_reference: model.default_reference}, null, 2)}</pre></details>
    {model.id === 'official' ? <p className="muted small">共享官方底模，由预训练资产统一管理。</p> : <button className="quiet danger" disabled={busy} onClick={onPreview}>查看删除范围</button>}
  </article>;
}

export function DeleteConfirmation({selection, busy, onCancel, onConfirm}: {selection: Selection; busy: boolean; onCancel: () => void; onConfirm: () => void}) {
  const blocked = selection.preview.references.length > 0 || selection.preview.deletable === false;
  return <div className="delete-preview" role="region" aria-label="删除确认"><div className="section-top"><h3>删除{kindName(selection.kind)}</h3><button className="quiet" data-delete-cancel disabled={busy} onClick={onCancel}>取消</button></div><p>{selection.title}</p>{selection.kind==='draft'&&<p className="small">删除此草稿记录和下面列出的整理副本。原始录音、已发布数据版本及其音频保留。</p>}<p className="muted small">以下是本次确认的文件范围：{selection.preview.files.length} 项 · {size(selection.preview.total_bytes)}</p><div className="delete-files"><PaginatedList items={selection.preview.files ?? []} label="删除文件" key={selection.id}>{(file, index) => <p key={file.path + index} className="path-text">{file.path} <span>{size(file.bytes)}</span></p>}</PaginatedList></div>
    {blocked ? <><p className="inline-error">此版本受到引用保护，暂不能删除。</p><div><PaginatedList items={selection.preview.references ?? []} label="保护引用" key={selection.id}>{(reference, index) => <p className="small path-text" key={reference.id + index}>{reference.reason} · {reference.kind} / {reference.id}</p>}</PaginatedList></div></> : <p className="muted small">未发现阻止删除的引用。确认后删除以上指定版本文件。</p>}
    <button className="danger" disabled={busy || blocked || !(selection.preview.preview_id ?? selection.preview.token)} onClick={onConfirm}>确认删除此{kindName(selection.kind)}</button>
  </div>;
}

export function Models({jobs, submit, reportError, onCatalogChange, showDatasets, refreshToken = 0}: {jobs: Job[]; submit: Submit; reportError: (message: string) => void; onCatalogChange: () => void; showDatasets: boolean; refreshToken?: number}) {
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [datasets, setDatasets] = useState<DatasetVersion[]>([]);
  const [drafts,setDrafts]=useState<{id:string;role:string;clips:unknown[];published?:string}[]>([]);
  const [gpt, setGpt] = useState('');
  const [sovits, setSovits] = useState('');
  const [role, setRole] = useState('');
  const [trusted, setTrusted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importId, setImportId] = useState('');
  const [selection, setSelection] = useState<Selection>();
  const [previewTarget, setPreviewTarget] = useState<PreviewTarget>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const previewDialog = useRef<HTMLDialogElement>(null);
  const previewRequest = useRef(0);
  const [notice, setNotice] = useState('');
  const [loadError, setLoadError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const jobsKey = jobs.map(job => `${job.id}:${job.state}`).join('|');
  useEffect(() => {
    const dialog = previewDialog.current;
    if (previewTarget && dialog) {
      if (!dialog.open) dialog.showModal();
      dialog.querySelector<HTMLButtonElement>('[data-delete-cancel]')?.focus();
    }
  }, [previewTarget, selection, previewError]);
  useEffect(() => () => {previewRequest.current++;}, []);
  const closePreview = () => {
    previewRequest.current++;
    previewDialog.current?.close();
    setPreviewTarget(undefined); setSelection(undefined); setPreviewLoading(false); setPreviewError('');
  };
  useEffect(() => {
    let disposed = false;
    Promise.all([api<ModelVersion[]>('/models'), api<DatasetVersion[]>('/datasets'),api<typeof drafts>('/dataset-drafts')]).then(([nextModels, nextDatasets, nextDrafts]) => {if (!disposed) {setModels(nextModels); setDatasets(nextDatasets); setDrafts(nextDrafts); setLoadError('');}}).catch(e => {if (!disposed) setLoadError(e.message);});
    return () => {disposed = true;};
  }, [jobsKey, refreshKey, showDatasets, refreshToken]);
  const importing = jobs.find(job => job.id === importId);
  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); reportError(''); setNotice('');
    try {await operation();} catch (e) {reportError((e as Error).message);} finally {setBusy(false);}
  };
  const preview = async (kind: Selection['kind'], id: string, title: string) => {
    const ticket = ++previewRequest.current;
    setPreviewTarget({kind, id, title}); setSelection(undefined); setPreviewLoading(true); setPreviewError(''); setNotice('');
    try {
      const result = await api<DeletePreview>(endpointFor(kind) + encodeURIComponent(id) + '/delete-preview');
      if (ticket === previewRequest.current) setSelection({kind, id, title, preview: result});
    } catch (e) {if (ticket === previewRequest.current) setPreviewError((e as Error).message);}
    finally {if (ticket === previewRequest.current) setPreviewLoading(false);}
  };
  const remove = async () => {
    if (!selection || selection.preview.references.length || selection.preview.deletable === false) return;
    const token = selection.preview.preview_id ?? selection.preview.token;
    if (!token) throw new Error('删除预览已失效，请重新查看删除范围。');
    const endpoint = endpointFor(selection.kind);
    await api(endpoint + encodeURIComponent(selection.id), {preview_id: token}, 'DELETE');
    setNotice(`已删除 ${selection.title} 的指定版本。`); closePreview(); setRefreshKey(value => value + 1); onCatalogChange();
  };
  const confirmDelete = async () => {
    setBusy(true); setPreviewError('');
    try {await remove();} catch (e) {setPreviewError((e as Error).message);} finally {setBusy(false);}
  };
  return <section className="panel model-management"><div className="section-top"><div><p className="eyebrow">MODELS & VERSIONS</p><h2>{showDatasets ? '模型与数据集管理' : '模型版本'}</h2></div><button className="quiet" disabled={busy} onClick={() => setRefreshKey(value => value + 1)}>刷新模型与版本</button></div>
    {loadError && <p className="inline-error">版本列表读取失败：{loadError}</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    <details><summary>导入本地 GPT 与 SoVITS v4 权重对</summary><div className="grid"><label>角色名称<input value={role} maxLength={80} onChange={e => setRole(e.target.value)}/></label><label>GPT 权重路径（.ckpt）<input value={gpt} placeholder="服务电脑上的 GPT 权重路径" onChange={e => setGpt(e.target.value)}/></label><label>SoVITS v4 权重路径（.pth）<input value={sovits} placeholder="服务电脑上的 SoVITS v4 权重路径" onChange={e => setSovits(e.target.value)}/></label></div><label className="check"><input type="checkbox" checked={trusted} onChange={e => setTrusted(e.target.checked)}/>这些本地权重来自我信任的来源</label><button disabled={busy || !trusted || !role.trim() || !gpt.trim() || !sovits.trim() || Boolean(importing && active(importing))} onClick={() => void perform(async () => {const job = await submit('/models/import', {gpt_path: gpt.trim(), sovits_path: sovits.trim(), role: role.trim(), trusted: true}); setImportId(job.id); setNotice('权重对导入已提交，将检查结构、版本和所需依赖。');})}>检查并导入权重对</button>{importing && <p className="muted small">导入任务 {importing.id.slice(0, 8)} · {stateLabel(importing.state)}</p>}</details>
    {models.length ? <div className="model-grid"><PaginatedList items={models ?? []} label="模型版本">{model => <ModelCard key={model.id} model={model} busy={busy} onPreview={() => void preview('model', model.id, `${model.role} · ${model.id.slice(0, 8)}`)}/>}</PaginatedList></div> : <div className="empty"><strong>还没有完整注册的模型包</strong><p>训练导出或导入兼容权重对后，版本会出现在这里。</p></div>}
    {showDatasets && <details><summary>已发布数据版本 · {datasets.length} 个</summary><PaginatedList items={datasets ?? []} label="管理数据版本">{version => <div className="version-row" key={version.id}><div><strong>{version.role}</strong><p className="muted small">{version.id.slice(0, 8)} · {Array.isArray(version.clips) ? version.clips.length : version.clips} 段</p></div><button className="quiet danger" disabled={busy} onClick={() => void preview('dataset', version.id, `${version.role} · ${version.id.slice(0, 8)}`)}>查看删除范围</button></div>}</PaginatedList></details>}
    <details open className="draft-management"><summary>整理后的标注草稿 · {drafts.length} 个</summary><p className="muted small">包含等待审核和已发布草稿。草稿与已发布版本分别管理。</p><PaginatedList items={drafts} label="管理标注草稿">{draft=><article className="version-row" key={draft.id}><div><strong>{draft.role}</strong><p className="muted small">{draft.id.slice(0,8)} · {draft.clips.length} 段 · {draft.published?'已发布':'等待审核'}</p></div><button className="quiet danger" disabled={busy} onClick={()=>void preview('draft',draft.id,`${draft.role} · 草稿 ${draft.id.slice(0,8)}`)}>查看删除范围</button></article>}</PaginatedList></details>
    {previewTarget && <dialog className="deletion-dialog" aria-label="删除范围" aria-busy={previewLoading || busy} ref={previewDialog} onCancel={event => {event.preventDefault(); if (!busy) closePreview();}}>
      {!selection && <div className="delete-dialog-status"><div className="section-top"><h3>查看{kindName(previewTarget.kind)}删除范围</h3><button className="quiet" data-delete-cancel disabled={busy} onClick={closePreview}>取消</button></div><p>{previewTarget.title}</p>{previewLoading && <p role="status">正在读取文件与引用，模型较大时请稍候…</p>}</div>}
      {previewError && <div className="delete-dialog-error"><p className="inline-error" role="alert">{previewError}</p><button disabled={busy} onClick={() => void preview(previewTarget.kind, previewTarget.id, previewTarget.title)}>重新查看删除范围</button></div>}
      {selection && <DeleteConfirmation selection={selection} busy={busy} onCancel={closePreview} onConfirm={() => void confirmDelete()}/>}
    </dialog>}
  </section>;
}
