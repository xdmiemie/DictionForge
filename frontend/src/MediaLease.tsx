import {useEffect, useState} from 'react';
import {api, ApiError} from './api';

type Lease = {token: string; expires_at: number};
export const isGone = (error: unknown) => error instanceof ApiError && error.status === 410;
export function useMediaLease(audioId?: string, enabled = true) {
  const [state, setState] = useState<{ready: boolean; error: string; gone: boolean}>({ready: false, error: '', gone: false});
  useEffect(() => {
    setState({ready: false, error: '', gone: false});
    if (!audioId || !enabled) return;
    let disposed = false, token: string | undefined, timer: ReturnType<typeof setInterval> | undefined;
    const release = () => {
      if (!token) return;
      const url = '/api/media-leases/' + encodeURIComponent(token) + '/release'; token = undefined;
      if (!navigator.sendBeacon?.(url, new Blob(['{}'], {type: 'application/json'}))) void fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}', keepalive: true}).catch(() => {});
    };
    const fail = (error: unknown) => {if (!disposed) {setState({ready: false, error: isGone(error) ? '这份临时音频已回收，无法播放或保存。冻结文本和任务记录仍保留。' : '音频引用获取失败：' + (error as Error).message, gone: isGone(error)}); release();}};
    const onHide = () => {release(); setState({ready: false, error: '播放器已释放，请重新打开。', gone: false});};
    const onDeleted = (event: Event) => {
      if ((event as CustomEvent<{deleted_media_ids?:string[]}>).detail.deleted_media_ids?.includes(audioId)) {
        disposed = true; release(); if (timer) clearInterval(timer);
        setState({ready:false, error:'音频已随关联对象删除。', gone:true});
      }
    };
    window.addEventListener('pagehide', onHide);
    window.addEventListener('dictionforge:catalog-deleted', onDeleted);
    void api<Lease>('/media-leases', {audio_id: audioId}).then(lease => {
      token = lease.token;
      if (disposed) {release(); return;}
      setState({ready: true, error: '', gone: false});
      timer = setInterval(() => {if (token) void api('/media-leases/' + encodeURIComponent(token) + '/heartbeat', {}).catch(fail);}, 20000);
    }).catch(fail);
    return () => {disposed = true; if (timer) clearInterval(timer); window.removeEventListener('pagehide', onHide); window.removeEventListener('dictionforge:catalog-deleted', onDeleted); release();};
  }, [audioId, enabled]);
  return state;
}

export function LeasedAudio({audioId, label}: {audioId: string; label: string}) {
  const [open, setOpen] = useState(false);
  const lease = useMediaLease(audioId, open);
  return <div className="leased-audio"><button className="quiet" onClick={() => setOpen(value => !value)}>{open ? '关闭作品播放器' : '打开作品播放器'}</button>
    {open && lease.ready && <audio controls preload="metadata" aria-label={label} src={'/api/audio/' + encodeURIComponent(audioId)}/>}
    {open && !lease.ready && <p className={lease.error ? 'inline-error small' : 'muted small'}>{lease.error || '正在获取音频引用…'}</p>}
    {open && lease.ready && <p className="muted small">播放器打开期间此作品受到引用保护；关闭后可删除。</p>}
  </div>;
}
