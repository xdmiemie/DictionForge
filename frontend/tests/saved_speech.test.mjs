import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
test('作品删除确认明确冻结集合、条数、跨页范围与引用保护', async () => {
  const vite = await createServer({root: fileURLToPath(new URL('../', import.meta.url)), server: {middlewareMode: true, hmr: false, ws: false}, appType: 'custom', optimizeDeps: {noDiscovery: true, include: []}});
  try {
    const {SavedDeleteConfirmation} = await vite.ssrLoadModule('/src/SavedSpeech.tsx');
    const html = renderToStaticMarkup(createElement(SavedDeleteConfirmation, {preview: {preview_id: 'frozen', count: 2, ids: ['a', 'b'], items: [{id: 'a', title: '第一条', files: ['a.wav']}, {id: 'b', title: '第二条', files: ['b.mp3']}], filter: {query: '测试'}}, scope: '当前筛选的全部结果（包含其他页）', busy: false, cancel() {}, confirm() {}}));
    assert.match(html, /确认删除 2 件作品/); assert.match(html, /包含其他页/); assert.match(html, /确认后新增的作品不包含/);
    assert.match(html, /data-preview-id="a"/); assert.match(html, /data-preview-id="b"/); assert.match(html, /引用的作品会保留/);
  } finally {await vite.close();}
});
