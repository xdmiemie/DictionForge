import {PaginatedList} from './Pagination';
import {useEffect,useState} from 'react';
import {active,api,Job,requestId,stateLabel} from './api';
import type {SpeechSentence} from './TextSynthesis';
type Target={eligible:boolean;reason?:string;role:string;dataset_id:string;target_version_id:string;source_model_id:string;config:{min_seconds:number;max_seconds:number}};
type Clip={id:string;audio_id:string;text:string;reviewed:boolean;excluded:boolean;seconds:number;label_source:string;asr_error?:string};
type Append={id:string;state:string;error?:string;asr_calls:number;added_count?:number;added_seconds?:number;job:Job;draft?:{id:string;revision:number;published?:string;clips:Clip[];rejected?:{start_sample:number;end_sample:number;seconds:number;reason:string}[]};training_defaults?:{version_id:string;initial_model:string}};
type Submit=(path:string,payload:object)=>Promise<Job>;
export type TrainSelection=(version:string,model:string)=>void;
const labels:Record<string,string>={REVIEW_REQUIRED:'等待逐段校对',FEATURIZING:'准备三段训练特征',LABELING:'整理标注',PUBLISHED:'新版本已发布',DUPLICATE:'内容已在数据集中'};
export function AppendSentence({ttsId,sentence,available,submit,onTrain}:{ttsId:string;sentence?:SpeechSentence;available:boolean;submit:Submit;onTrain?:TrainSelection}) {
  const [target,setTarget]=useState<Target>(),[rows,setRows]=useState<Append[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [maximum,setMaximum]=useState(12),[minimum,setMinimum]=useState(1),[changes,setChanges]=useState<Record<string,Partial<Clip>>>({});
  const refresh=async()=>{setRows(await api<Append[]>('/augmentations?tts_id='+encodeURIComponent(ttsId)));};
  useEffect(()=>{let alive=true;setChanges({});setError('');void api<Target>('/tts/'+ttsId+'/append-target').then(value=>{if(alive){setTarget(value);if(value.config){setMaximum(value.config.max_seconds);setMinimum(value.config.min_seconds);}}}).catch(e=>{if(alive)setError(e.message);}); const read=()=>api<Append[]>('/augmentations?tts_id='+encodeURIComponent(ttsId)).then(value=>{if(alive)setRows(value);}).catch(e=>{if(alive)setError(e.message);});void read();const timer=setInterval(()=>void read(),3000);return()=>{alive=false;clearInterval(timer);};},[ttsId]);
  const perform=async(action:()=>Promise<void>)=>{setBusy(true);setError('');try{await action();await refresh();}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  const change=(clip:Clip,value:Partial<Clip>)=>setChanges(current=>({...current,[clip.id]:{...current[clip.id],...value}}));
  return <section className="append-sentence" aria-label="当前句回流"><h3>将当前句加入数据集</h3>
    {error&&<p className="inline-error" role="alert">{error}</p>}
    {!target?<p>正在核对来源数据…</p>:!target.eligible?<p className="muted">{target.reason}</p>:<>
      <p>目标角色：<strong>{target.role}</strong> · 从版本 {target.target_version_id.slice(0,8)} 的最新后继追加</p>
      <p className="reference-transcript">选中第 {(sentence?.index??0)+1} 句：{sentence?.text??'请先选择一句'}</p>
      <details><summary>回流切分规则</summary><div className="grid"><label>最短片段（秒）<input type="number" min="0.5" max="30" step="0.1" value={minimum} onChange={e=>setMinimum(e.target.valueAsNumber)}/></label><label>最长片段（秒）<input type="number" min="0.5" max="30" step="0.1" value={maximum} onChange={e=>setMaximum(e.target.valueAsNumber)}/></label></div><p className="muted small">完整保留一句可沿用原文本；拆成多片会逐段识别并等待校对。新增片段只进入训练集，真实留出集保持不变。</p></details>
      <button disabled={busy||!sentence||!available||rows.some(row=>active(row.job))} onClick={()=>void perform(async()=>{await submit('/tts/'+ttsId+'/append',{sentence_id:sentence!.sentence_id,target_version_id:target.target_version_id,split_config:{min_seconds:minimum,max_seconds:maximum}});})}>将当前句加入数据集</button>
      {!available&&<p className="muted small">当前临时音频已失效或播放器未打开；已受理的追加记录仍可继续校对。</p>}
    </>}
    <PaginatedList items={rows ?? []} label="回流记录" key={ttsId}>{row=><article className="feature-card" key={row.id} style={{display:'block'}}><h4>{labels[row.state]??stateLabel(row.state)} · {row.id.slice(0,8)}</h4><p className="muted small">追加任务：{stateLabel(row.job.state)} · ASR处理片段：{row.asr_calls}</p>{row.error&&<p className="inline-error">{row.error}</p>}
      {row.draft&&!row.draft.published&&!active(row.job)&&<><p>请逐段试听、修正文字并勾选确认，或排除不合格片段。</p><PaginatedList items={row.draft.clips ?? []} label={"回流片段 " + row.id.slice(0,8)} key={row.draft.id}>{clip=>{const value={...clip,...changes[clip.id]};return <div className="review-clip" key={clip.id}><audio controls preload="none" aria-label={'回流片段 '+clip.id} src={'/api/audio/'+clip.audio_id}/><label>片段标注<textarea disabled={busy} value={value.text} onChange={e=>change(clip,{text:e.target.value,reviewed:false})}/></label><label className="check"><input type="checkbox" disabled={busy} checked={value.reviewed} onChange={e=>change(clip,{reviewed:e.target.checked})}/>已试听并确认标注</label><label className="check"><input type="checkbox" disabled={busy} checked={value.excluded} onChange={e=>change(clip,{excluded:e.target.checked})}/>排除此片段</label><p className="muted small">{clip.seconds.toFixed(2)}秒 · 标注来源 {clip.label_source}{clip.asr_error&&' · '+clip.asr_error}</p></div>;}}</PaginatedList><div className="actions">
        <button disabled={busy} onClick={()=>void perform(async()=>{await api('/dataset-drafts/'+row.draft!.id,{revision:row.draft!.revision,clips:row.draft!.clips.filter(c=>changes[c.id]).map(c=>({id:c.id,...changes[c.id]}))},'PATCH');setChanges(current=>Object.fromEntries(Object.entries(current).filter(([id])=>!row.draft!.clips.some(clip=>clip.id===id))));})}>保存回流校对</button>
        <button disabled={busy} onClick={()=>void perform(async()=>{await submit('/dataset-drafts/'+row.draft!.id+'/asr',{clip_ids:row.draft!.clips.filter(c=>!c.reviewed&&!c.excluded).map(c=>c.id)});})}>重新识别未确认片段</button>
        <button disabled={busy||Object.keys(changes).length>0||row.draft.clips.filter(c=>!c.excluded).some(c=>!c.reviewed||!c.text.trim())} onClick={()=>void perform(async()=>{await submit('/augmentations/'+row.id+'/publish',{revision:row.draft!.revision});})}>准备特征并发布回流版本</button>
      </div></>}
      {!!row.draft?.rejected?.length&&<details><summary>切分规则未保留的区间</summary><PaginatedList items={row.draft.rejected ?? []} label={"未保留区间 " + row.id.slice(0,8)} key={row.draft.id}>{item=><p key={item.start_sample}>{(item.start_sample/48000).toFixed(2)}–{(item.end_sample/48000).toFixed(2)}秒：{item.reason}</p>}</PaginatedList></details>}
      {row.training_defaults&&<><p className="success-note">新增 {row.added_count??0} 段 · {(row.added_seconds??0).toFixed(2)}秒 · 数据版本 {row.training_defaults.version_id.slice(0,8)}</p><button className="primary-button" onClick={()=>onTrain?.(row.training_defaults!.version_id,row.training_defaults!.initial_model)}>用新版本微调</button></>}
    </article>}</PaginatedList>
  </section>;
}
