import {finiteNumber, optimizerUpdates, peakBytes, Progress} from './api';

export function TaskProgress({progress, training = false}: {progress?: Progress; training?: boolean}) {
  if (!progress) return <p className="muted">等待工作进程报告进度</p>;
  const steps = optimizerUpdates(progress);
  const peak = peakBytes(progress);
  const value = progress.unit === 'epochs' || progress.unit === '轮' ? progress.completed_epochs : steps ?? progress.completed;
  const total = finiteNumber(progress.target) ? progress.target : progress.total;
  const units: Record<string, string> = {optimizer_updates: '次优化器更新', epochs: '轮', check: '项检查'};
  const unit = progress.unit ? units[progress.unit] ?? progress.unit : '';
  const phases: Record<string, string> = {s2: 'SoVITS v4 LoRA · S2', s1: 'GPT 微调 · S1', scan: '扫描录音', slice: '语音切分', diagnostic: '任务诊断'};
  const phase = progress.stage ?? progress.phase;
  return <div className="task-progress">
    {phase && <p>{phases[phase] ?? phase}</p>}
    {finiteNumber(value) && finiteNumber(total) && total > 0 ? <><progress aria-label="任务进度" max={total} value={value}/><p className="progress-caption">{value} / {total} {unit}</p></> : <p className="muted">{finiteNumber(value) ? `已完成 ${value} ${unit}` : '正在处理'}</p>}
    <div className="metrics">
      {finiteNumber(progress.epoch) && <span>当前 epoch {progress.epoch}</span>}
      {finiteNumber(progress.completed_epochs) && <span>已完成 {progress.completed_epochs} 轮</span>}
      {finiteNumber(steps) && <span>累计优化器更新 {steps}</span>}
      {finiteNumber(progress.processed_batches) && <span>已处理 batch {progress.processed_batches}</span>}
      {finiteNumber(progress.physical_batch_size) && <span>物理 batch {progress.physical_batch_size}</span>}
      {finiteNumber(progress.skipped_updates) && <span>跳过更新 {progress.skipped_updates}</span>}
      {finiteNumber(progress.loss) ? <span>Loss {progress.loss.toPrecision(4)}</span> : progress.loss === null && <span>Loss 非有限，已省略</span>}
      {finiteNumber(progress.learning_rate) && <span>LR {progress.learning_rate.toExponential(2)}</span>}
      {finiteNumber(peak) && <span>{training ? '实际训练显存峰值' : '任务显存峰值'} {(peak / 2 ** 30).toFixed(2)} GiB</span>}
    </div>
  </div>;
}
