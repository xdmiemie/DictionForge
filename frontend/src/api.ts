export type JobError = {code?: string; message: string; stage?: string; retryable?: boolean};
type Metric = number | null;
export type Progress = {completed_sentences?: Metric; total_sentences?: Metric; sentence_index?: Metric; phase?: string; stage?: string; completed?: Metric; total?: Metric; unit?: string; step?: Metric; optimizer_updates?: Metric; target?: Metric; epoch?: Metric; completed_epochs?: Metric; processed_batches?: Metric; physical_batch_size?: Metric; skipped_updates?: Metric; loss?: Metric; learning_rate?: Metric; peak_cuda_bytes?: Metric; peak_allocated_bytes?: Metric};
export const finiteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export const optimizerUpdates = (progress: Progress) => finiteNumber(progress.step) ? progress.step : finiteNumber(progress.optimizer_updates) ? progress.optimizer_updates : undefined;
export const peakBytes = (progress: Progress) => finiteNumber(progress.peak_cuda_bytes) ? progress.peak_cuda_bytes : finiteNumber(progress.peak_allocated_bytes) ? progress.peak_allocated_bytes : undefined;
export type Job = {id: string; kind: string; state: string; payload?: Record<string, unknown>; result?: Record<string, unknown>; error?: string | JobError; created?: number; updated?: number; latest_progress?: Progress};
export type JobEvent = {id?: number; event_seq?: number; schema_version?: number; kind?: string; event_type?: string; stage?: string; payload: Record<string, unknown>};
export class ApiError extends Error {constructor(message: string, public status: number) {super(message);}}

export async function api<T>(path: string, payload?: unknown, method?: string): Promise<T> {
  const response = await fetch('/api' + path, {method: method ?? (payload === undefined ? 'GET' : 'POST'), headers: payload === undefined ? undefined : {'Content-Type': 'application/json'}, body: payload === undefined ? undefined : JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) {
    const error = data.error ?? data.detail ?? data;
    throw new ApiError(typeof error === 'string' ? error : error.message ?? JSON.stringify(error), response.status);
  }
  return data as T;
}

export const requestId = () => ({request_id: crypto.randomUUID()});
export const active = (job: Job) => ['QUEUED', 'PREPARING', 'RUNNING', 'PAUSE_REQUESTED', 'CHECKPOINTING', 'RESUMING', 'STOP_REQUESTED', 'FINALIZING'].includes(job.state);
const states: Record<string, string> = {OFFICIAL: '官方底模', IMPORTED: '已导入', SNAPSHOTTING: '正在冻结快照', WRITING: '正在写入模型包', VALIDATING: '正在校验', PUBLISHED: '已发布', PENDING: '尚未开始', SKIPPED: '已跳过', QUEUED: '排队中', PREPARING: '准备中', RUNNING: '运行中', PAUSE_REQUESTED: '等待安全边界', SAVING: '正在写入恢复点', CHECKPOINTING: '正在保存', PAUSED: '已暂停', RESUMING: '恢复中', STOP_REQUESTED: '等待停止', FINALIZING: '正在完成', COMPLETED: '已完成', EARLY_COMPLETED: '已提前完成', INTERRUPTED: '已中断', FAILED: '失败', CANCELLED: '已取消'};
export const stateLabel = (state: string) => states[state] ?? state;
const kinds: Record<string, string> = {diagnostic: '任务诊断', system_probe: '运行环境诊断', probe: '运行环境诊断', asset_prepare: '准备预训练资产', assets_prepare: '准备预训练资产', assets: '准备预训练资产', dataset_scan: '扫描录音', dataset_prepare: '整理数据集', dataset_asr: '片段重新识别', asr: '片段重新识别', features: '准备训练特征', preflight: '训练预检', training_preflight: '训练预检', train: '微调训练', export: '模型导出', model_export: '模型包导出', model_import: '导入权重对', model_probe: '模型效果试听', tts: '语音合成', speech_save: '保存语音作品', augmentation_prepare: "整理当前句回流", augmentation_publish: "发布回流版本", dataset_append: '当前句回流'};
export const kindLabel = (kind: string) => kinds[kind] ?? kind;
export const errorText = (error?: string | JobError) => typeof error === 'string' ? error : error?.message;
