// One authorized UI-to-GPU P09 submission. A durable guard prevents an accidental
// second valid POST. --resume only inspects the already recorded accepted job.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'data', 'p09-browser-submit');
const marker = path.join(output, 'submission.json');
const runDir = path.join(output, new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(runDir, {recursive: true});
const {chromium} = require(path.resolve(path.dirname(process.execPath), '../node_modules/playwright'));
const base = 'http://127.0.0.1:7867';
const sourceText = '你好，今天我们一起去公园散步。';
const editedText = '这是一条编辑中的新句子，已受理的语音仍使用原文。';
const modelId = 'a974c5da69695d0aba3477bff5c7b768';
const parameters = {seed: 2026, sample_steps: 16, top_k: 5, top_p: 1, temperature: 1, speed_factor: 1.15, repetition_penalty: 1.35, fragment_interval: .3};
const resume = process.argv.includes('--resume');
const report = {stage: 'P09', started: new Date().toISOString(), runDir, mode: resume ? 'resume-existing' : 'one-valid-submit', checks: [], screenshots: [], submitted: [], pageErrors: [], consoleErrors: [], httpErrors: [], blockedRequests: [], requestFailures: []};
let context, page, saved;
const pass = (name, detail) => report.checks.push({name, status: 'PASS', detail});
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const get = async endpoint => {const response = await context.request.get(base + '/api' + endpoint); assert.equal(response.status(), 200, endpoint); return response.json();};
const screenshot = async (name, locator) => {const file = path.join(runDir, name + '.png'); await locator.screenshot({path: file}); report.screenshots.push({path: file, sha256: sha(file)});};
const play = async locator => locator.evaluate(async element => {
  const events = [];
  for (const name of ['play', 'playing', 'pause', 'error']) element.addEventListener(name, () => events.push(name));
  element.muted = true;
  await Promise.race([element.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('play timeout')), 15000))]);
  await new Promise(resolve => setTimeout(resolve, 350));
  const value = {src: element.currentSrc, duration: element.duration, currentTime: element.currentTime, events, muted: true, subjectiveListening: 'NOT_PERFORMED'};
  element.pause(); return value;
});
const assertFrozen = snapshot => {
  assert.equal(snapshot.request.text, sourceText);
  assert.equal(snapshot.request.source_text, sourceText);
  assert.equal(snapshot.request.model_version_id, modelId);
  assert.deepEqual(snapshot.request.parameters, parameters);
  assert.equal(snapshot.request.split_rule, 'punctuation');
  assert.equal(snapshot.request.reference_fingerprint, saved.before.selection.reference_fingerprint);
};

(async () => {
  try {
    if (resume) {assert.ok(fs.existsSync(marker), '没有已受理的提交记录'); saved = JSON.parse(fs.readFileSync(marker, 'utf8')); assert.ok(saved.jobId, '上次提交是否受理未知，须先核对，不能自动重发');}
    else assert.equal(fs.existsSync(marker), false, '已有唯一提交记录，禁止第二条有效 POST；使用 --resume 只验收原任务');
    const temp = path.join(runDir, 'temp'); fs.mkdirSync(temp);
    context = await chromium.launchPersistentContext(path.join(runDir, 'profile'), {executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, viewport: {width: 1440, height: 1100}, locale: 'zh-CN', env: {...process.env, TEMP: temp, TMP: temp, TMPDIR: temp}, args: ['--no-first-run', '--no-default-browser-check']});
    report.browser = context.browser()?.version();
    report.playwright = require(path.resolve(path.dirname(process.execPath), '../node_modules/playwright/package.json')).version;
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      let allowed = ['GET', 'HEAD'].includes(request.method());
      if (!resume && request.method() === 'POST' && url.pathname === '/api/tts' && !fs.existsSync(marker)) {
        const body = request.postDataJSON();
        const exact = body.text === sourceText && body.model_version_id === modelId && body.language === 'zh' && body.split_rule === 'punctuation' && JSON.stringify(body.parameters) === JSON.stringify(parameters);
        if (exact) {allowed = true; report.submitted.push(body); saved.payload = body; fs.writeFileSync(marker, JSON.stringify(saved, null, 2), {flag: 'wx'});}
      }
      if (url.origin !== base || !allowed) {report.blockedRequests.push({method: request.method(), url: request.url()}); return route.abort('blockedbyclient');}
      return route.continue();
    });
    page = context.pages()[0]; page.setDefaultTimeout(30000);
    page.on('console', item => {if (item.type() === 'error') report.consoleErrors.push({text: item.text(), url: item.location().url});});
    page.on('pageerror', error => report.pageErrors.push(error.stack ?? String(error)));
    page.on('response', response => {if (response.status() >= 400) report.httpErrors.push({status: response.status(), url: response.url()});});
    page.on('requestfailed', request => report.requestFailures.push({url: request.url(), error: request.failure()?.errorText}));
    if (!resume) {
      const selection = await get('/synthesis/selection'), current = await get('/tts/current'), jobs = await get('/jobs');
      assert.equal(selection.active_model, modelId); assert.equal(selection.enabled, true); assert.equal(selection.valid, true);
      assert.equal(jobs.filter(job => ['QUEUED', 'RUNNING'].includes(job.state)).length, 0, '开始前无活跃应用作业');
      saved = {before: {selection, currentId: current.id, currentAudio: current.result.audio, jobIds: jobs.map(job => job.id).sort()}, sourceText, editedText, parameters, originalRunDir: runDir};
    }
    report.before = saved.before;
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    const panel = page.locator('.text-synthesis');
    await page.getByLabel('选择模型版本', {exact: true}).selectOption(modelId);
    if (!resume) {
      await panel.getByLabel('待朗读文本', {exact: true}).fill(sourceText);
      await panel.getByLabel('语速倍率', {exact: true}).fill('1.15');
      await panel.locator('.synthesis-advanced > summary').click();
      await panel.getByLabel('基础随机种子', {exact: true}).fill('2026');
      await panel.getByLabel('采样步数', {exact: true}).selectOption('16');
      await screenshot('01-valid-submission-form', panel);
      const responsePromise = page.waitForResponse(response => response.url() === base + '/api/tts' && response.request().method() === 'POST');
      await panel.getByRole('button', {name: '生成语音', exact: true}).click();
      const response = await responsePromise; assert.ok(response.ok(), await response.text());
      const accepted = await response.json(); saved.jobId = accepted.id; saved.accepted = accepted;
      fs.writeFileSync(marker, JSON.stringify(saved, null, 2));
      pass('实际页面按钮提交一条有效语音请求', {jobId: accepted.id, payload: saved.payload, state: accepted.state});
    }
    report.jobId = saved.jobId;
    await panel.locator(`[data-result-id="${saved.jobId}"]`).waitFor();
    await panel.getByLabel('待朗读文本', {exact: true}).fill(editedText);
    const frozen = await get('/tts/' + saved.jobId); assertFrozen(frozen);
    assert.equal(await panel.locator('.frozen-text').textContent(), sourceText);
    assert.equal(await panel.getByLabel('待朗读文本', {exact: true}).inputValue(), editedText);
    pass('受理后编辑不改变冻结文本或非默认参数', {jobId: saved.jobId, request: frozen.request, editedText});
    await screenshot('02-accepted-frozen-text', panel);
    let finished = frozen, previousState;
    const deadline = Date.now() + 300000;
    while (['QUEUED', 'RUNNING', 'CANCELLING'].includes(finished.job.state) && Date.now() < deadline) {
      if (previousState !== finished.job.state) {console.log(JSON.stringify({jobId: saved.jobId, state: finished.job.state, phase: finished.job.latest_progress?.phase})); previousState = finished.job.state;}
      await page.waitForTimeout(1500); finished = await get('/tts/' + saved.jobId);
    }
    report.snapshot = finished;
    assert.equal(finished.job.state, 'COMPLETED', JSON.stringify(finished.job.error ?? finished.result)); assertFrozen(finished);
    assert.equal(finished.result.audio.sample_rate, 48000); assert.equal(finished.result.sentences.length, 1);
    assert.equal(finished.result.sentences[0].seed, 2026); assert.equal(finished.result.sentences[0].actual_seed, 2026);
    const resultPanel = panel.locator(`[data-result-id="${saved.jobId}"]`);
    await resultPanel.locator('.complete-speech-audio').waitFor({timeout: 15000});
    const completePlayback = await play(resultPanel.locator('.complete-speech-audio'));
    assert.ok(completePlayback.events.includes('playing') && completePlayback.currentTime > 0);
    await resultPanel.getByRole('button', {name: '选择第 1 句并定位', exact: true}).click();
    assert.equal(await resultPanel.locator('.speech-sentence[aria-current="true"]').getAttribute('data-sentence-id'), finished.result.sentences[0].sentence_id);
    assert.equal(await resultPanel.locator('.frozen-text mark').textContent(), sourceText);
    const sentencePlayback = await play(resultPanel.getByLabel('第 1 句音频', {exact: true}));
    assert.ok(sentencePlayback.events.includes('playing'));
    pass('真实GPU完成并播放完整音频与独立句', {audio: finished.result.audio, sentence: finished.result.sentences[0], completePlayback, sentencePlayback});
    await screenshot('03-completed-playable-result', resultPanel);
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    await panel.locator(`[data-result-id="${saved.jobId}"] .complete-speech-audio`).waitFor();
    assert.equal(await panel.locator('.frozen-text').textContent(), sourceText);
    assert.equal(await panel.getByLabel('待朗读文本', {exact: true}).inputValue(), editedText);
    const current = await get('/tts/current'); assert.equal(current.id, saved.jobId); assertFrozen(current);
    pass('刷新恢复新current与独立编辑草稿', {currentId: current.id, audioSha256: current.result.audio.sha256});
    await screenshot('04-refreshed-current', panel);
    const selection = await get('/synthesis/selection'), jobs = await get('/jobs'), old = await get('/tts/' + saved.before.currentId);
    assert.deepEqual(selection, saved.before.selection);
    assert.deepEqual(jobs.map(job => job.id).sort(), [...saved.before.jobIds, saved.jobId].sort());
    assert.deepEqual(old.result.audio, saved.before.currentAudio);
    assert.equal(report.submitted.length, resume ? 0 : 1); assert.equal(report.blockedRequests.length, 0); assert.equal(report.pageErrors.length, 0); assert.equal(report.consoleErrors.length, 0); assert.equal(report.httpErrors.length, 0);
    pass('仅新增指定一条任务，模型参考及旧结果保持不变', {jobId: saved.jobId, activeModel: selection.active_model, referenceFingerprint: selection.reference_fingerprint, oldCurrentId: old.id});
    report.after = {currentId: current.id, selection, jobIds: jobs.map(job => job.id).sort()}; report.status = 'PASS';
  } catch (error) {report.status = 'FAILED'; report.error = error.stack ?? String(error); process.exitCode = 1; if (page) await screenshot('failure', page.locator('body')).catch(() => {});}
  finally {
    if (context) await context.close(); report.finished = new Date().toISOString();
    const json = JSON.stringify(report, null, 2); fs.writeFileSync(path.join(runDir, 'report.json'), json); fs.writeFileSync(path.join(output, 'latest.json'), json);
    console.log(JSON.stringify({status: report.status, checks: report.checks.length, jobId: report.jobId, error: report.error, report: path.join(runDir, 'report.json')}, null, 2));
  }
})();
