import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('试听卡仅播放完成结果并分开保留模型来源、条件与错误', async () => {
  const vite = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom', optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {ReferencePreview, ComparisonAudioCard} = await vite.ssrLoadModule('/src/Synthesis.tsx');
    const reference = {id: 'ref-id', audio_id: 'ref-audio', text: '参考实际说出的文本。', language: 'zh', seconds: 4.25, sample_rate: 48000};
    const preview = renderToStaticMarkup(createElement(ReferencePreview, {reference}));
    assert.match(preview, /参考实际说出的文本/);
    assert.match(preview, /4\.25 秒/);
    assert.match(preview, /src="\/api\/audio\/ref-audio"/);
    const invalid = renderToStaticMarkup(createElement(ReferencePreview, {reference: {...reference, seconds: null, sample_rate: Infinity}}));
    assert.doesNotMatch(invalid, /NaN|Infinity/);
    const result = {audio: {audio_id: 'result-audio', seconds: 2.5, sample_rate: 48000}, model_version_id: 'trained-pair', conditions_fingerprint: 'same-conditions', parameters: {seed: 42, sample_steps: 32}};
    const card = state => renderToStaticMarkup(createElement(ComparisonAudioCard, {label: '自训模型', job: {id: 'probe-id', kind: 'model_probe', state, result, error: state === 'FAILED' ? {message: '参考已改变', code: 'REFERENCE_CHANGED'} : undefined}}));
    assert.match(card('COMPLETED'), /src="\/api\/audio\/result-audio"/);
    assert.match(card('COMPLETED'), /48 kHz/);
    assert.match(card('COMPLETED'), /trained-pair/);
    assert.match(card('COMPLETED'), /same-conditions/);
    assert.doesNotMatch(card('RUNNING'), /<audio/);
    assert.doesNotMatch(card('FAILED'), /<audio/);
    assert.match(card('FAILED'), /参考已改变/);
  } finally {await vite.close();}
});
