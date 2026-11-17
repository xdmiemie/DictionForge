import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

test('模型卡区分结构校验和 GPU 试听，删除确认展示确切范围并保护引用', async () => {
  const vite = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom',
    optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {ModelCard, DeleteConfirmation} = await vite.ssrLoadModule('/src/Models.tsx');
    const model = {id: 'model-version', version_id: 'model-version', model_id: 'role-model', role: '测试角色', status: 'READY',
      weights: {gpt: {sha256: 'gpt-hash', bytes: 1024}, sovits: {sha256: 'sovits-hash', bytes: 2048}},
      validation: {structure: 'PASS', gpu: 'NOT_RUN'}, stage_progress: {s1: {state: 'PENDING'}, s2: {state: 'RUNNING', optimizer_updates: 2}}, provenance: {initial_model: 'official'}};
    const html = renderToStaticMarkup(createElement(ModelCard, {model, busy: false, onPreview() {}}));
    assert.match(html, /结构检查 <strong>通过/);
    assert.match(html, /GPU 试听 <strong>尚未运行/);
    assert.match(html, /导出时阶段进度（冻结快照）/);
    assert.match(html, /GPT · 尚未开始 · 使用初始化权重（官方底模）/);
    assert.match(html, /SoVITS v4 · 运行中 · 更新 2/);
    const official = renderToStaticMarkup(createElement(ModelCard, {model: {...model, id: 'official'}, busy: false, onPreview() {}}));
    assert.match(official, /共享官方底模/);
    assert.doesNotMatch(official, /查看删除范围/);
    const selection = {kind: 'model', id: model.id, title: model.role, preview: {token: 'frozen-token', deletable: true,
      files: [{path: 'managed/model/gpt.ckpt', bytes: 1024}], references: [], total_bytes: 1024}};
    const render = value => renderToStaticMarkup(createElement(DeleteConfirmation, {selection: value, busy: false, onConfirm() {}, onCancel() {}}));
    assert.match(render(selection), /managed\/model\/gpt.ckpt/);
    assert.match(render(selection), /<button class="danger">确认删除此模型版本/);
    const protectedSelection = {...selection, preview: {...selection.preview, references: [{kind: 'run', id: 'dependent-run', reason: '被派生运行引用'}]}};
    assert.match(render(protectedSelection), /被派生运行引用/);
    assert.match(render(protectedSelection), /<button class="danger" disabled="">确认删除此模型版本/);
    assert.match(render({...selection, preview: {...selection.preview, token: undefined}}), /<button class="danger" disabled="">确认删除此模型版本/);
  } finally {await vite.close();}
});
