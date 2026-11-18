import {useState} from 'react';
import {PaginatedList} from './Pagination';
import {errorText, type JobError} from './api';

export type ReviewClip = {id:string; audio_id:string; text:string; reviewed:boolean; excluded:boolean; seconds:number; asr_error?:string | JobError | null};
export type ReviewChange = {id:string; text?:string; reviewed?:boolean; excluded?:boolean};

export function ReviewWizard({clips, locked, published, onChange, onCommit}: {
  clips: ReviewClip[]; locked: boolean; published?: boolean;
  onChange: (id:string, change:Omit<ReviewChange,'id'>) => void;
  onCommit: (changes:ReviewChange[]) => Promise<void>;
}) {
  const [index,setIndex]=useState<number|null>(null), [saving,setSaving]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const pending=clips.filter(c=>!c.excluded&&!c.reviewed), excluded=clips.filter(c=>c.excluded).length;
  const current=index===null?undefined:clips[index], disabled=locked||saving;
  const commit=async(changes:ReviewChange[],next:number|null)=>{
    setSaving(true);setError('');setNotice('');
    try {
      await onCommit(changes);setIndex(next);
      if(next===null){
        const retained=clips.map(clip=>({...clip,...changes.find(change=>change.id===clip.id)})).filter(clip=>!clip.excluded);
        setNotice(retained.length?'审核已结束，标注已保存，可发布数据版本。':'所有片段已去掉，标注已保存。请恢复需要的片段后再发布。');
      }
    }
    catch(e){setError((e as Error).message);}finally{setSaving(false);}
  };
  const decide=(remove:boolean)=>{
    if(!current||index===null)return;
    const after=index+1<clips.length?index+1:-1;
    const before=clips.findIndex((c,i)=>i<index&&!c.reviewed&&!c.excluded);
    const next=after>=0?after:before>=0?before:null;
    void commit([{id:current.id,reviewed:!remove,excluded:remove}],next);
  };
  const approveRemaining=async()=>{
    const empty=clips.findIndex(c=>!c.excluded&&!c.text.trim());
    if(empty>=0){setError('有片段尚未识别出文字，请补写、重新识别或去掉后再默认通过。');setIndex(empty);return;}
    await commit(clips.filter(c=>!c.excluded&&!c.reviewed).map(c=>({id:c.id,reviewed:true})),null);
  };
  return <div className="review-wizard" role="region" aria-label="文字审核窗口">
    <p className="muted small">语音识别已生成文字。可逐条试听、修正后通过，或跳过审核让全部保留片段默认通过。去掉只从本数据版本排除，不删除原始录音。</p>
    <div className="data-summary"><span>共 {clips.length} 条</span><span>待审核 {pending.length} 条</span><span>已去掉 {excluded} 条</span></div>
    {error&&<p className="inline-error" role="alert">{error}</p>}{notice&&<p className="success-note" role="status">{notice}</p>}
    {!published&&<div className="actions">
      {index===null?<><button disabled={disabled||!clips.length} onClick={()=>{setIndex(clips.findIndex(c=>!c.excluded&&!c.reviewed)>=0?clips.findIndex(c=>!c.excluded&&!c.reviewed):0);setNotice('');}}>开始逐条审核</button><button disabled={disabled||!pending.length} onClick={()=>void approveRemaining()}>跳过审核，全部默认通过</button></>:<button disabled={disabled} onClick={()=>void approveRemaining()}>结束审核，剩余默认通过</button>}
    </div>}
    {current&&!published&&<article className="review-clip" data-review-id={current.id}>
      <div className="section-top"><h3>审核第 {index!+1} / {clips.length} 条</h3><span className="pill">{current.excluded?'已去掉':current.reviewed?'已通过':'待审核'}</span></div>
      <audio key={current.id} controls preload="none" aria-label="当前审核音频" src={'/api/audio/'+encodeURIComponent(current.audio_id)}/>
      <label>识别文字<textarea aria-label="审核文案" disabled={disabled} value={current.text} onChange={e=>onChange(current.id,{text:e.target.value,reviewed:false})}/></label>
      <p className="muted small">{current.seconds.toFixed(2)} 秒</p>
      {current.asr_error&&<p className="inline-error">识别提示：{errorText(current.asr_error)}</p>}
      <div className="actions"><button className="danger" disabled={disabled} onClick={()=>decide(true)}>去掉数据，下一条</button><button className="primary-button" disabled={disabled||!current.text.trim()} onClick={()=>decide(false)}>通过，下一条</button></div>
      <div className="actions"><button disabled={disabled||index===0} onClick={()=>setIndex(index!-1)}>上一条</button><button disabled={disabled||index===clips.length-1} onClick={()=>setIndex(index!+1)}>下一条</button></div>
    </article>}
    <details className="review-overview"><summary>全部标注与审核状态 · {clips.length} 条</summary>
      <PaginatedList items={clips} label="标注总览">{(clip,i)=><article className="review-summary" key={clip.id}><div className="section-top"><strong>片段 {i+1} · {clip.excluded?'已去掉':clip.reviewed?'已通过':'待审核'}</strong>{!published&&<button disabled={disabled} onClick={()=>setIndex(i)}>审核此条</button>}</div><p>{clip.text||'尚无识别文字'}</p>{published&&<audio controls preload="none" src={'/api/audio/'+encodeURIComponent(clip.audio_id)}/>}</article>}</PaginatedList>
    </details>
  </div>;
}
