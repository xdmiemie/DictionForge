// Playback/UI acceptance for existing P09 GPU outputs. Only the explicitly
// invalid punctuation-only POST is allowed; no valid GPU work is submitted.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'data', 'p09-browser');
const runDir = path.join(output, new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(runDir, {recursive: true});
const runtime = path.resolve(path.dirname(process.execPath), '../node_modules/playwright');
const {chromium} = require(runtime);
const base = 'http://127.0.0.1:7867';
const report = {stage: 'P09', started: new Date().toISOString(), runDir, checks: [], screenshots: [], consoleErrors: [], pageErrors: [], httpErrors: [], requestFailures: [], blockedRequests: [], submitted: []};
let context, page;
const pass = (name, detail) => report.checks.push({name, status: 'PASS', detail});
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const get = async endpoint => {const response = await context.request.get(base + '/api' + endpoint); assert.equal(response.status(), 200, endpoint); return response.json();};
const screenshot = async (name, locator) => {const file = path.join(runDir, name + '.png'); if (locator) await locator.screenshot({path: file}); else await page.screenshot({path: file, fullPage: false}); report.screenshots.push({path: file, sha256: sha(file)});};
const play = async locator => locator.evaluate(async element => {
  const events = [];
  for (const name of ['loadedmetadata', 'canplay', 'play', 'playing', 'pause', 'error']) element.addEventListener(name, () => events.push(name));
  element.muted = true;
  await Promise.race([element.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('play timeout')), 15000))]);
  await new Promise(resolve => setTimeout(resolve, 300));
  return {src: element.currentSrc, duration: element.duration, currentTime: element.currentTime, readyState: element.readyState, events, muted: true, subjectiveListening: 'NOT_PERFORMED'};
});

(async () => {
  try {
    const acceptance = JSON.parse(fs.readFileSync(path.join(root, 'data', 'p09-http.json'), 'utf8'));
    assert.equal(acceptance.outcome, 'PASS', '先完成四组真实 GPU 验收');
    report.httpEvidence = {path: path.join(root, 'data', 'p09-http.json'), sha256: sha(path.join(root, 'data', 'p09-http.json'))};
    const temp = path.join(runDir, 'temp'); fs.mkdirSync(temp);
    context = await chromium.launchPersistentContext(path.join(runDir, 'profile'), {executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
      viewport: {width: 1440, height: 1100}, locale: 'zh-CN', env: {...process.env, TEMP: temp, TMP: temp, TMPDIR: temp}, args: ['--no-first-run', '--no-default-browser-check']});
    report.playwright = require(path.join(runtime, 'package.json')).version; report.browser = context.browser()?.version();
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      let allowed = ['GET', 'HEAD'].includes(request.method());
      if (request.method() === 'POST' && url.pathname === '/api/tts') {
        const body = request.postDataJSON();
        allowed = body.text === '？！……' && body.language === 'zh';
        if (allowed) report.submitted.push(body);
      }
      if (url.origin !== base || !allowed) {report.blockedRequests.push({method: request.method(), url: request.url()}); return route.abort('blockedbyclient');}
      return route.continue();
    });
    page = context.pages()[0]; page.setDefaultTimeout(30000);
    page.on('console', item => {if (item.type() === 'error') report.consoleErrors.push({text: item.text(), url: item.location().url});});
    page.on('pageerror', error => report.pageErrors.push(error.stack ?? String(error)));
    page.on('response', response => {if (response.status() >= 400) report.httpErrors.push({status: response.status(), url: response.url()});});
    page.on('requestfailed', request => report.requestFailures.push({url: request.url(), error: request.failure()?.errorText}));
    const before = await get('/tts/current'), beforeJobs = await get('/jobs');
    report.before = {currentId: before.id, jobIds: beforeJobs.map(job => job.id).sort(), audioSha256: before.result.audio.sha256};
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    const panel = page.locator('.text-synthesis');
    await panel.locator(`[data-result-id="${before.id}"]`).waitFor();
    assert.equal(await panel.getByLabel('目标语言', {exact: true}).inputValue(), 'zh');
    assert.equal(await panel.getByLabel('目标语言', {exact: true}).isDisabled(), true);
    const sourceText = await panel.locator('.frozen-text').textContent();
    assert.equal(sourceText, before.request.text);
    await panel.getByLabel('待朗读文本', {exact: true}).fill('🌙只修改下一次输入，不改变已经冻结的语音结果。');
    assert.equal(await panel.locator('.frozen-text').textContent(), sourceText);
    await panel.locator('.synthesis-advanced > summary').click();
    await panel.getByLabel('断句方式', {exact: true}).selectOption('lines');
    await panel.getByLabel('语速倍率', {exact: true}).fill('1.15');
    await panel.getByLabel('基础随机种子', {exact: true}).fill('123');
    assert.equal((await get('/tts/current')).id, before.id);
    pass('中文输入与参数编辑不改冻结结果', {id: before.id, frozenText: sourceText, input: await panel.getByLabel('待朗读文本', {exact: true}).inputValue()});
    await screenshot('01-input-and-frozen-result', panel);

    const history = panel.getByLabel('查看合成任务', {exact: true});
    const cases = {};
    for (const name of ['single', 'repeated', 'lines', 'none']) {
      const snapshot = acceptance.cases[name].snapshot;
      cases[name] = snapshot;
      await history.selectOption(snapshot.id === before.id ? '' : snapshot.id);
      const resultPanel = panel.locator(`[data-result-id="${snapshot.id}"]`);
      await resultPanel.waitFor();
      assert.equal(await resultPanel.locator('.frozen-text').textContent(), snapshot.request.text);
      assert.equal(await resultPanel.locator('.speech-sentence').count(), snapshot.result.sentences.length);
      const played = await play(resultPanel.locator('.complete-speech-audio'));
      assert.ok(played.events.includes('playing') && played.currentTime > 0);
      assert.equal(snapshot.result.audio.sample_rate, 48000);
      await resultPanel.locator('.complete-speech-audio').evaluate(element => element.pause());
      pass(`真实 ${name} 结果浏览器播放`, {id: snapshot.id, splitRule: snapshot.request.split_rule, sentenceCount: snapshot.result.sentences.length, played});
      if (name === 'lines' || name === 'none') await screenshot(name === 'lines' ? '04-lines-result' : '05-none-result', resultPanel);
    }

    const repeated = cases.repeated;
    await history.selectOption(repeated.id === before.id ? '' : repeated.id);
    const resultPanel = panel.locator(`[data-result-id="${repeated.id}"]`);
    await resultPanel.waitFor();
    const main = resultPanel.locator('.complete-speech-audio');
    const sentences = repeated.result.sentences;
    assert.equal(sentences[0].text, sentences[2].text);
    assert.notEqual(sentences[0].sentence_id, sentences[2].sentence_id);
    await play(main);
    await resultPanel.getByRole('button', {name: '选择第 2 句并定位', exact: true}).click();
    await page.waitForFunction(({id, sentenceId}) => document.querySelector(`[data-result-id="${id}"] .speech-sentence[aria-current="true"]`)?.getAttribute('data-sentence-id') === sentenceId, {id: repeated.id, sentenceId: sentences[1].sentence_id});
    const seekPosition = await main.evaluate(element => element.currentTime);
    assert.ok(seekPosition >= sentences[1].start_sample / 48000 - .05 && seekPosition < sentences[1].end_sample / 48000);
    const independent = resultPanel.getByLabel('第 3 句音频', {exact: true});
    await play(independent);
    assert.equal(await main.evaluate(element => element.paused), true);
    await page.waitForFunction(({id, sentenceId}) => document.querySelector(`[data-result-id="${id}"] .speech-sentence[aria-current="true"]`)?.getAttribute('data-sentence-id') === sentenceId, {id: repeated.id, sentenceId: sentences[2].sentence_id});
    await resultPanel.getByRole('button', {name: '选择第 2 句并定位', exact: true}).click();
    assert.equal(await independent.evaluate(element => element.paused), true, '选新句必须停止旧独立句声音');
    await page.waitForFunction(({id, sentenceId}) => document.querySelector(`[data-result-id="${id}"] .speech-sentence[aria-current="true"]`)?.getAttribute('data-sentence-id') === sentenceId, {id: repeated.id, sentenceId: sentences[1].sentence_id});
    assert.equal(await resultPanel.locator('.frozen-text mark').textContent(), Array.from(repeated.request.text).slice(sentences[1].source_start, sentences[1].source_end).join(''));
    await screenshot('02-repeated-sentence-selection', resultPanel);
    // Let actual playback cross a boundary; no synthetic timeupdate is dispatched.
    await main.evaluate((element, seconds) => {element.currentTime = seconds;}, sentences[1].start_sample / 48000 - .1);
    await play(main);
    await page.waitForFunction(({id, sentenceId}) => document.querySelector(`[data-result-id="${id}"] .speech-sentence[aria-current="true"]`)?.getAttribute('data-sentence-id') === sentenceId, {id: repeated.id, sentenceId: sentences[1].sentence_id});
    await main.evaluate(element => element.pause());
    pass('逐句定位、独立句互斥及完整播放跨句同步', {id: repeated.id, secondSeekSeconds: seekPosition, selectedSentenceId: sentences[1].sentence_id, selectedSourceRange: [sentences[1].source_start, sentences[1].source_end], repeatedIds: [sentences[0].sentence_id, sentences[2].sentence_id]});

    await page.reload({waitUntil: 'domcontentloaded'});
    await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    await panel.locator(`[data-result-id="${before.id}"]`).waitFor();
    assert.equal(await panel.locator('.frozen-text').textContent(), before.request.text);
    assert.ok((await panel.getByLabel('待朗读文本', {exact: true}).inputValue()).startsWith('🌙只修改'));
    pass('刷新恢复服务端当前结果并保留独立编辑文本', {id: before.id});

    await panel.getByLabel('待朗读文本', {exact: true}).fill('？！……');
    const invalidResponse = page.waitForResponse(response => response.url() === base + '/api/tts' && response.status() === 422);
    await panel.getByRole('button', {name: '生成语音', exact: true}).click();
    await invalidResponse;
    await panel.getByRole('alert').waitFor();
    assert.equal((await get('/tts/current')).id, before.id);
    assert.equal(await panel.locator('.frozen-text').textContent(), before.request.text);
    const afterInvalidPlayback = await play(panel.locator('.complete-speech-audio'));
    await panel.locator('.complete-speech-audio').evaluate(element => element.pause());
    assert.ok(afterInvalidPlayback.events.includes('playing'));
    pass('真实无效 POST 返回422并保留当前有效结果', {error: await panel.getByRole('alert').innerText(), currentId: before.id, playedAfterRejection: afterInvalidPlayback});
    await screenshot('06-invalid-request-preserved', panel);
    await page.setViewportSize({width: 390, height: 844});
    await panel.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, document.querySelector('.text-synthesis').getBoundingClientRect().top + window.scrollY));
    await screenshot('07-narrow-text-synthesis');
    const dimensions = await page.evaluate(() => ({viewport: window.innerWidth, scroll: document.documentElement.scrollWidth}));
    assert.equal(dimensions.viewport, dimensions.scroll); pass('390px 窄屏文本与结果布局', dimensions);

    const after = await get('/tts/current'), afterJobs = await get('/jobs');
    report.after = {currentId: after.id, jobIds: afterJobs.map(job => job.id).sort(), audioSha256: after.result.audio.sha256};
    assert.deepEqual(report.after, report.before);
    assert.equal(report.submitted.length, 1); assert.equal(report.blockedRequests.length, 0); assert.equal(report.pageErrors.length, 0);
    assert.equal(report.httpErrors.filter(item => !(item.url === base + '/api/tts' && item.status === 422)).length, 0);
    assert.equal(report.consoleErrors.filter(item => !(item.url === base + '/api/tts' && item.text.includes('422'))).length, 0);
    pass('无新增GPU任务、浏览器未捕获异常', '仅1次预期无效POST；HTTP422及对应console资源错误按预期记录，其他HTTP/console/page异常为零。');
    report.status = 'PASS';
  } catch (error) {report.status = 'FAILED'; report.error = error.stack ?? String(error); process.exitCode = 1; if (page) await screenshot('failure').catch(() => {});}
  finally {
    if (context) await context.close();
    report.finished = new Date().toISOString();
    const json = JSON.stringify(report, null, 2); fs.writeFileSync(path.join(runDir, 'report.json'), json); fs.writeFileSync(path.join(output, 'latest.json'), json);
    console.log(JSON.stringify({status: report.status, checks: report.checks.length, error: report.error, report: path.join(runDir, 'report.json')}, null, 2));
  }
})();
