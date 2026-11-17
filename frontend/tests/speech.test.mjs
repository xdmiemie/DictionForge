import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('逐句播放使用 codepoint 范围及完整 PCM 半开区间，重复句保持独立身份', async () => {
  const vite = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom', optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {codepointSlice, sentenceAtTime, FrozenText, SpeechPlayback, SpeechFailurePanel} = await vite.ssrLoadModule('/src/TextSynthesis.tsx');
    const text = '🌙你好。你好。\n下一句。';
    const sentences = [
      {sentence_id: 'first', index: 0, text: '🌙你好。', source_start: 0, source_end: 4, start_sample: 0, end_sample: 48001, audio: {sample_rate: 48000, audio_id: 'first-audio'}},
      {sentence_id: 'second', index: 1, text: '你好。', source_start: 4, source_end: 7, start_sample: 48001, end_sample: 96003, actual_seed: 43, audio: {sample_rate: 48000, audio_id: 'second-audio'}},
      {sentence_id: 'third', index: 2, text: '下一句。', source_start: 8, source_end: 12, start_sample: 96003, end_sample: 144000, audio: {sample_rate: 48000, audio_id: 'third-audio'}},
    ];
    for (const sentence of sentences) assert.equal(codepointSlice(text, sentence.source_start, sentence.source_end), sentence.text);
    assert.notEqual(text.slice(4, 7), sentences[1].text, 'fixture must distinguish UTF-16 from codepoints');
    assert.match(renderToStaticMarkup(createElement(FrozenText, {text, sentence: sentences[1]})), /🌙你好。<mark>你好。<\/mark>\n下一句。/);
    assert.equal(sentenceAtTime(sentences, 0, 48000).sentence_id, 'first');
    assert.equal(sentenceAtTime(sentences, 48000 / 48000, 48000).sentence_id, 'first');
    assert.equal(sentenceAtTime(sentences, 48001 / 48000, 48000).sentence_id, 'second');
    assert.equal(sentenceAtTime(sentences, 96003 / 48000, 48000).sentence_id, 'third');
    assert.equal(sentenceAtTime(sentences, 3, 48000).sentence_id, 'third');
    assert.equal(sentenceAtTime(sentences, 3.1, 48000), undefined);
    assert.equal(sentenceAtTime(sentences, NaN, 48000), undefined);
    const snapshot = {id: 'frozen-job', job: {id: 'frozen-job', state: 'COMPLETED', kind: 'tts'}, request: {text, language: 'zh', model_id: 'frozen-model'}, result: {audio: {audio_id: 'whole', sample_rate: 48000, seconds: 3}, sentences}};
    const html = renderToStaticMarkup(createElement(SpeechPlayback, {snapshot}));
    assert.match(html, /frozen-model/);
    assert.match(html, /data-sentence-id="second"/);
    assert.match(html, /src="\/api\/audio\/whole"/);
    assert.match(html, /src="\/api\/audio\/second-audio"/);
    assert.match(html, /实际种子 43/);
    assert.doesNotMatch(renderToStaticMarkup(createElement(SpeechPlayback, {snapshot: {...snapshot, job: {...snapshot.job, state: 'FAILED'}}})), /<audio/);
    const failure = renderToStaticMarkup(createElement(SpeechFailurePanel, {result: {error: 'CUDA out of memory', phase: 'text_frontend', sentence_index: 1, sentence_text: '你好。', actual_seed: 43}}));
    assert.match(failure, /文本前处理 · 第 2 句/);
    assert.match(failure, /失败句文本：你好。/);
  } finally {await vite.close();}
});
