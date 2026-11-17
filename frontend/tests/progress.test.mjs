import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('训练进度容忍 null/非有限指标并保留真实旧字段计数', async () => {
  // This SSR-only check never serves the browser: do not start a dependency
  // crawler that can race server.close() after the assertions have finished.
  const vite = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom',
    optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {TaskProgress} = await vite.ssrLoadModule('/src/Progress.tsx');
    const {RunStageCard} = await vite.ssrLoadModule('/src/Training.tsx');
    const metrics = {stage: 's1', unit: 'optimizer_updates', target: 2, step: null, optimizer_updates: 1, loss: null, learning_rate: null, peak_cuda_bytes: null, peak_allocated_bytes: 2 ** 30, processed_batches: 4, skipped_updates: 2};
    for (const html of [renderToStaticMarkup(createElement(TaskProgress, {progress: metrics, training: true})), renderToStaticMarkup(createElement(RunStageCard, {name: 's1', stage: metrics, current: true}))]) {
      assert.match(html, /value="1"/);
      assert.match(html, /1\.00.*GiB/);
      assert.match(html, /非有限，已省略/);
      assert.doesNotMatch(html, /NaN|Infinity/);
    }
    const nonFinite = {...metrics, loss: Infinity, learning_rate: NaN, peak_allocated_bytes: Infinity};
    assert.doesNotMatch(renderToStaticMarkup(createElement(TaskProgress, {progress: nonFinite})), /NaN|Infinity|GiB/);
    assert.doesNotMatch(renderToStaticMarkup(createElement(RunStageCard, {name: 's1', stage: nonFinite, current: true})), /NaN|Infinity|GiB/);
    for (const unit of ['epochs', '轮']) {
      const epoch = {...metrics, unit, completed_epochs: 0, optimizer_updates: 9, target: 1};
      assert.match(renderToStaticMarkup(createElement(TaskProgress, {progress: epoch})), /max="1" value="0"/);
      assert.match(renderToStaticMarkup(createElement(RunStageCard, {name: 's1', stage: epoch, current: true})), /max="1" value="0"/);
    }
  } finally {await vite.close();}
});
