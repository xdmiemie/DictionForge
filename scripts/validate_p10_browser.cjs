const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict'), crypto = require('node:crypto');
const root = path.resolve(__dirname, '..'), runDir = path.join(root, 'data/p10-browser', new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(runDir, {recursive: true});
const {chromium} = require(path.resolve(path.dirname(process.execPath), '../node_modules/playwright'));
const base = 'http://127.0.0.1:7867', prefix = 'P10_UI_' + Date.now();
const protectedIds = ['bfa6b69242ae406f9b04859837d763e3', '7c4fff962a0a4f46b171b16f7c939e41', 'b2968e66c6294d3d9f654b2f0b2768b9'];
const report = {started: new Date().toISOString(), runDir, prefix, checks: [], created: [], previews: [], confirmed: [], leases: [], heartbeat: [], releases: [], pageErrors: [], consoleErrors: [], httpErrors: [], blocked: [], screenshots: []};
const owned = new Set(), approvedPreviews = new Set(); let page, context;
const pass = (name, detail) => report.checks.push({name, status: 'PASS', detail});
const get = async route => {const response = await context.request.get(base + '/api' + route); assert.equal(response.status(), 200); return response.json();};
const shot = async (name, locator) => {const file = path.join(runDir, name + '.png'); await locator.screenshot({path: file}); report.screenshots.push({path: file, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')});};
const play = async locator => locator.evaluate(async audio => {audio.muted = true; const events = []; audio.addEventListener('playing', () => events.push('playing')); await audio.play(); await new Promise(resolve => setTimeout(resolve, 300)); audio.pause(); return {events, duration: audio.duration, currentTime: audio.currentTime, subjectiveListening: 'NOT_PERFORMED'};});
async function clickPreview(button, expected) {
  const pending = page.waitForResponse(response => response.url().endsWith('/api/saved-speech/delete-preview') && response.request().method() === 'POST'); await button.click();
  const response = await pending; assert.equal(response.status(), 200); const value = await response.json();
  assert.deepEqual([...value.ids].sort(), [...expected].sort()); assert.equal(value.count, expected.length);
  for (const id of value.ids) assert.ok(owned.has(id), 'preview must only include this run own works');
  approvedPreviews.add(value.preview_id); report.previews.push(value);
  await page.getByRole('region', {name: '作品删除确认'}).waitFor();
  assert.equal(await page.locator('[data-preview-id]').count(), expected.length); return value;
}
async function confirmDelete() {
  const pending = page.waitForResponse(response => response.url().endsWith('/api/saved-speech/delete') && response.request().method() === 'POST');
  await page.getByRole('region', {name: '作品删除确认'}).getByRole('button', {name: /^确认删除这/}).click();
  const response = await pending; assert.equal(response.status(), 200); const value = await response.json(); report.confirmed.push(value); return value;
}
(async () => {
  try {
    const temp = path.join(runDir, 'temp'); fs.mkdirSync(temp);
    context = await chromium.launchPersistentContext(path.join(runDir, 'profile'), {executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, viewport: {width: 1440, height: 1100}, locale: 'zh-CN', acceptDownloads: true, env: {...process.env, TEMP: temp, TMP: temp, TMPDIR: temp}, args: ['--no-first-run', '--no-default-browser-check']});
    report.browser = context.browser()?.version();
    const beforeCurrent = await get('/tts/current'), beforeLibrary = await get('/saved-speech?page_size=10');
    report.before = {current: beforeCurrent.id, total: beforeLibrary.total};
    const protectedBefore = beforeLibrary.items.filter(item => protectedIds.includes(item.id)); assert.equal(protectedBefore.length, 3);
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()); let allowed = ['GET', 'HEAD'].includes(request.method());
      if (request.method() === 'POST') {
        const body = request.postData() ? request.postDataJSON() : {};
        if (/^\/api\/media-leases(\/[^/]+\/(heartbeat|release))?$/.test(url.pathname)) allowed = true;
        else if (url.pathname === `/api/tts/${beforeCurrent.id}/save`) allowed = body.title?.startsWith(prefix) && ['wav', 'mp3'].includes(body.format);
        else if (url.pathname === '/api/saved-speech/delete-preview') allowed = body.all_filtered ? body.query === prefix : body.ids?.length > 0 && body.ids.every(id => owned.has(id));
        else if (url.pathname === '/api/saved-speech/delete') allowed = approvedPreviews.has(body.preview_id);
      }
      if (url.origin !== base || !allowed) {report.blocked.push({url: request.url(), method: request.method()}); return route.abort();} return route.continue();
    });
    page = context.pages()[0]; page.setDefaultTimeout(30000);
    page.on('pageerror', error => report.pageErrors.push(String(error))); page.on('console', message => {if (message.type() === 'error') report.consoleErrors.push(message.text());});
    page.on('response', async response => {
      const url = response.url(); if (response.status() >= 400) report.httpErrors.push({url, status: response.status()});
      if (url.endsWith('/api/media-leases') && response.request().method() === 'POST' && response.ok()) report.leases.push(await response.json());
      if (/\/media-leases\/[^/]+\/heartbeat$/.test(url)) report.heartbeat.push({url, status: response.status()});
      if (/\/media-leases\/[^/]+\/release$/.test(url)) report.releases.push({url, status: response.status()});
    });
    await page.goto(base); await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    const synthesis = page.locator('.text-synthesis'), library = page.getByRole('region', {name: '已保存语音', exact: true});
    await synthesis.locator('.complete-speech-audio').waitFor();
    assert.ok((await play(synthesis.locator('.complete-speech-audio'))).events.includes('playing'));
    const releasing = page.waitForResponse(response => /\/media-leases\/[^/]+\/release$/.test(response.url()));
    await synthesis.getByRole('button', {name: '关闭当前播放器', exact: true}).click(); await releasing;
    assert.equal(await synthesis.locator('audio').count(), 0); assert.equal(await synthesis.getByRole('button', {name: '保存到作品库'}).isDisabled(), true);
    await synthesis.getByRole('button', {name: '打开当前播放器'}).click(); await synthesis.locator('.complete-speech-audio').waitFor();
    pass('临时播放器取得引用后播放，关闭释放并撤销src，重开可用', {current: beforeCurrent.id});
    for (let index = 0; index < 14; index++) {
      const format = index === 1 ? 'mp3' : 'wav', title = `${prefix}_${String(index).padStart(2, '0')}_${format}`;
      await synthesis.getByLabel('作品标题', {exact: true}).fill(title); await synthesis.getByLabel('保存格式', {exact: true}).selectOption(format);
      const waiting = page.waitForResponse(response => response.url().endsWith('/save') && response.request().method() === 'POST'); await synthesis.getByRole('button', {name: '保存到作品库', exact: true}).click();
      const response = await waiting; assert.ok(response.ok()); const job = await response.json(); let finished;
      for (let attempt = 0; attempt < 90; attempt++) {finished = await get('/jobs/' + job.id); if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(finished.state)) break; await page.waitForTimeout(500);}
      assert.equal(finished.state, 'COMPLETED', JSON.stringify(finished.error)); const record = finished.result.saved_speech; assert.ok(record.title.startsWith(prefix)); owned.add(record.id); report.created.push(record);
    }
    pass('真实页面保存14件独立作品含WAV和320kbps MP3', report.created.map(item => ({id: item.id, format: item.format, sha256: item.audio.sha256, path: item.audio.path})));
    await library.getByLabel('作品关键词筛选', {exact: true}).fill(prefix);
    await page.waitForFunction(() => document.querySelectorAll('.saved-speech-card').length === 10);
    assert.match(await library.locator('.pagination').innerText(), /共 14 件/);
    await library.getByRole('button', {name: '全选本页', exact: true}).click();
    assert.equal(await library.locator('input[type=checkbox]:checked').count(), 10);
    await library.getByRole('button', {name: '下一页', exact: true}).click(); await page.waitForFunction(() => document.querySelectorAll('.saved-speech-card').length === 4);
    assert.equal(await library.locator('input[type=checkbox]:checked').count(), 0);
    await library.locator('input[type=checkbox]').first().check(); await library.getByLabel('作品模型筛选', {exact: true}).selectOption(beforeCurrent.request.model_id);
    await page.waitForFunction(() => document.querySelectorAll('.saved-speech-card').length === 10); assert.equal(await library.locator('input[type=checkbox]:checked').count(), 0);
    pass('模型及标题筛选、10条分页、翻页及筛选清空按钮选择', {total: 14, firstPage: 10, secondPage: 4}); await shot('01-library-pagination', library);
    const mp3 = report.created.find(item => item.format === 'mp3');
    await library.getByLabel('作品关键词筛选', {exact: true}).fill(mp3.title); await page.waitForFunction(() => document.querySelectorAll('.saved-speech-card').length === 1);
    const card = library.locator('[data-saved-id="' + mp3.id + '"]');
    await card.getByRole('button', {name: '打开作品播放器'}).click(); await card.locator('audio').waitFor(); const played = await play(card.locator('audio')); assert.ok(played.events.includes('playing'));
    const downloadEvent = page.waitForEvent('download'); await card.getByRole('link', {name: '下载 MP3', exact: true}).click(); const download = await downloadEvent; const downloadPath = path.join(runDir, 'download.mp3'); await download.saveAs(downloadPath);
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(downloadPath)).digest('hex'), mp3.audio.sha256);
    const range = await context.request.get(base + '/api/audio/' + mp3.audio.audio_id, {headers: {Range: 'bytes=0-99'}}); assert.equal(range.status(), 206); assert.equal((await range.body()).length, 100);
    pass('永久MP3真实解码播放、浏览器下载及Range', {played, downloadPath, contentType: range.headers()['content-type'], contentRange: range.headers()['content-range']});
    await clickPreview(card.getByRole('button', {name: '删除此作品'}), [mp3.id]); const protectedResult = await confirmDelete(); assert.equal(protectedResult.counts.protected, 1);
    await card.waitFor();
    // Refresh remounts the protected card, so its lease is released by unmount.
    await card.getByRole('button', {name: '打开作品播放器'}).waitFor();
    await clickPreview(card.getByRole('button', {name: '删除此作品'}), [mp3.id]); const single = await confirmDelete(); assert.equal(single.counts.deleted, 1);
    pass('正在打开的作品受保护，释放后单件精确删除', {id: mp3.id, protectedResult, single});
    await library.getByLabel('作品关键词筛选', {exact: true}).fill(prefix); await page.waitForFunction(() => document.querySelectorAll('.saved-speech-card').length === 10);
    const two = await library.locator('.saved-speech-card').evaluateAll(items => items.slice(0, 2).map(item => item.getAttribute('data-saved-id')));
    await library.locator('input[type=checkbox]').nth(0).check(); await library.locator('input[type=checkbox]').nth(1).check();
    await clickPreview(library.getByRole('button', {name: '预览删除本页所选（2）', exact: true}), two); const selected = await confirmDelete(); assert.equal(selected.counts.deleted, 2);
    const remaining = report.created.map(item => item.id).filter(id => id !== mp3.id && !two.includes(id)); assert.equal(remaining.length, 11);
    await library.getByRole('button', {name: '预览删除当前筛选全部（11）', exact: true}).waitFor();
    await clickPreview(library.getByRole('button', {name: '预览删除当前筛选全部（11）', exact: true}), remaining); await shot('02-cross-page-confirmation', library);
    // A save arriving after confirmation preview must not expand or dismiss it.
    await synthesis.getByLabel('作品标题', {exact: true}).fill(prefix + '_after_preview');
    await synthesis.getByLabel('保存格式', {exact: true}).selectOption('wav');
    const lateResponse = page.waitForResponse(response => response.url().endsWith('/save') && response.request().method() === 'POST');
    await synthesis.getByRole('button', {name: '保存到作品库', exact: true}).click();
    const lateJob = await (await lateResponse).json(); let lateFinished;
    for (let attempt = 0; attempt < 90; attempt++) {lateFinished = await get('/jobs/' + lateJob.id); if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(lateFinished.state)) break; await page.waitForTimeout(500);}
    assert.equal(lateFinished.state, 'COMPLETED'); const lateRecord = lateFinished.result.saved_speech; owned.add(lateRecord.id); report.created.push(lateRecord);
    await library.getByRole('button', {name: '预览删除当前筛选全部（12）', exact: true}).waitFor();
    assert.equal(await library.locator('[data-preview-id]').count(), 11);
    assert.deepEqual((await library.locator('[data-preview-id]').evaluateAll(items => items.map(item => item.getAttribute('data-preview-id')))).sort(), [...remaining].sort());
    await shot('02b-frozen-confirmation-after-save', library);
    const all = await confirmDelete(); assert.equal(all.counts.deleted, 11);
    pass('本页所选和跨页全部只删除确认的自建集合', {pageSelected: two, acrossPages: remaining, all});
    const afterFrozen = await get('/saved-speech?query=' + prefix); assert.equal(afterFrozen.total, 1); assert.equal(afterFrozen.items[0].id, lateRecord.id);
    pass('预览后新保存作品不清空或扩张确认集合，确认后新作品仍在', {newId: lateRecord.id, frozenIds: remaining});
    const lateCard = library.locator('[data-saved-id="' + lateRecord.id + '"]'); await lateCard.waitFor();
    await clickPreview(lateCard.getByRole('button', {name: '删除此作品'}), [lateRecord.id]); assert.equal((await confirmDelete()).counts.deleted, 1);
    assert.equal((await get('/saved-speech?query=' + prefix)).total, 0);
    const afterLibrary = await get('/saved-speech?page_size=10'); assert.equal(afterLibrary.total, beforeLibrary.total); assert.deepEqual(afterLibrary.items.filter(item => protectedIds.includes(item.id)), protectedBefore);
    await synthesis.getByLabel('查看合成任务', {exact: true}).selectOption('70f2d6554c8548afa3d8bab86a1effba');
    await synthesis.getByText(/这份临时音频已回收/).waitFor(); assert.equal(await synthesis.locator('audio').count(), 0); assert.equal(await synthesis.getByRole('button', {name: '保存到作品库'}).count(), 0);
    await shot('03-expired-history', synthesis); pass('已回收历史保留文本并明确禁播放及保存', {id: '70f2d6554c8548afa3d8bab86a1effba'});
    await synthesis.getByRole('button', {name: '返回当前合成结果'}).click(); await synthesis.locator('.complete-speech-audio').waitFor();
    if (!report.heartbeat.length) await page.waitForTimeout(21000); assert.ok(report.heartbeat.some(item => item.status === 200)); pass('真实20秒引用心跳成功', report.heartbeat);
    await page.setViewportSize({width: 390, height: 844}); await library.scrollIntoViewIfNeeded(); await shot('04-narrow-library', library);
    const dimensions = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth})); assert.equal(dimensions.width, dimensions.scroll);
    assert.equal((await get('/tts/current')).id, beforeCurrent.id); assert.equal(report.blocked.length, 0); assert.equal(report.pageErrors.length, 0); assert.equal(report.consoleErrors.length, 0); assert.equal(report.httpErrors.length, 0);
    pass('3件原作品/current保持不变、窄屏无溢出、浏览器无错误', dimensions); report.status = 'PASS';
  } catch (error) {report.status = 'FAILED'; report.error = error.stack ?? String(error); process.exitCode = 1; if (page) await shot('failure', page.locator('body')).catch(() => {});}
  finally {if (context) await context.close(); report.finished = new Date().toISOString(); const json = JSON.stringify(report, null, 2); fs.writeFileSync(path.join(runDir, 'report.json'), json); fs.writeFileSync(path.join(root, 'data/p10-browser/latest.json'), json); console.log(JSON.stringify({status: report.status, checks: report.checks.length, created: report.created.length, error: report.error, report: path.join(runDir, 'report.json')}, null, 2));}
})();
