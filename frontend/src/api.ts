export type JobError = {code?: string; message: string; stage?: string; retryable?: boolean};
export type Progress = {phase?: string; stage?: string; completed?: number; total?: number; unit?: string; step?: number; target?: number; epoch?: number; loss?: number; learning_rate?: number; peak_cuda_bytes?: number};
export type Job = {id: string; kind: string; state: string; payload?: Record<string, unknown>; result?: Record<string, unknown>; error?: string | JobError; created?: number; updated?: number; latest_progress?: Progress};
export type JobEvent = {id?: number; event_seq?: number; schema_version?: number; kind?: string; event_type?: string; stage?: string; payload: Record<string, unknown>};

export async function api<T>(path: string, payload?: unknown, method?: string): Promise<T> {
  const response = await fetch('/api' + path, {method: method ?? (payload === undefined ? 'GET' : 'POST'), headers: payload === undefined ? undefined : {'Content-Type': 'application/json'}, body: payload === undefined ? undefined : JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) {
    const error = data.error ?? data.detail ?? data;
    throw new Error(typeof error === 'string' ? error : error.message ?? JSON.stringify(error));
  }
  return data as T;
}

export const requestId = () => ({request_id: crypto.randomUUID()});
export const active = (job: Job) => ['QUEUED', 'PREPARING', 'RUNNING', 'PAUSE_REQUESTED', 'CHECKPOINTING', 'RESUMING', 'STOP_REQUESTED', 'FINALIZING'].includes(job.state);
const states: Record<string, string> = {QUEUED: '排队中', PREPARING: '准备中', RUNNING: '运行中', PAUSE_REQUESTED: '等待安全边界', CHECKPOINTING: '正在保存', PAUSED: '已暂停', RESUMING: '恢复中', STOP_REQUESTED: '等待停止', FINALIZING: '正在完成', COMPLETED: '已完成', EARLY_COMPLETED: '已提前完成', INTERRUPTED: '已中断', FAILED: '失败', CANCELLED: '已取消'};
export const stateLabel = (state: string) => states[state] ?? state;
const kinds: Record<string, string> = {diagnostic: '环境诊断', dataset_scan: '扫描录音', dataset_prepare: '整理数据集', features: '准备训练特征', train: '微调训练', export: '模型导出', tts: '语音合成', dataset_append: '当前句回流'};
export const kindLabel = (kind: string) => kinds[kind] ?? kind;
export const errorText = (error?: string | JobError) => typeof error === 'string' ? error : error?.message;
