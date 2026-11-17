// P08 real UI acceptance. Uploads one owned reference and toggles logical selection.
// Never starts GPU jobs; existing comparisons supply the actual inference results.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'data', 'p08-browser');
const runDir = path.join(output, new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(runDir, {recursive: true});
const runtime = path.resolve(path.dirname(process.execPath), '../node_modules/playwright');
const {chromium} = require(runtime);
const base = 'http://127.0.0.1:7867';
const testModelId = process.env.DICTIONFORGE_BROWSER_MODEL || 'official';
const report = {stage: 'P08', started: new Date().toISOString(), runDir, testModelId, checks: [], screenshots: [],
  playwright: require(path.join(runtime, 'package.json')).version, consoleErrors: [], pageErrors: [], httpErrors: [], requestFailures: [], blockedRequests: [], mutations: []};
let context, page;
let originalSelection;
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const pass = (name, detail) => report.checks.push({name, status: 'PASS', detail});
const get = async endpoint => {const response = await context.request.get(base + '/api' + endpoint); assert.equal(response.status(), 200, endpoint); return response.json();};
const screenshot = async (name, locator) => {const file = path.join(runDir, name + '.png'); if (locator) await locator.screenshot({path: file}); else await page.screenshot({path: file, fullPage: false}); report.screenshots.push({path: file, sha256: sha(file)});};
const playback = async locator => locator.evaluate(async element => {
  const events = [];
  for (const name of ['loadedmetadata', 'canplay', 'play', 'playing', 'error']) element.addEventListener(name, () => events.push(name));
  element.muted = true;
  await Promise.race([element.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('audio timeout')), 15000))]);
  await new Promise(resolve => setTimeout(resolve, 350)); element.pause();
  return {src: element.currentSrc, duration: element.duration, currentTime: element.currentTime, readyState: element.readyState, events, muted: true, subjectiveListening: 'NOT_PERFORMED'};
});

(async () => {
  try {
    const temp = path.join(runDir, 'temp'); fs.mkdirSync(temp);
    context = await chromium.launchPersistentContext(path.join(runDir, 'profile'), {executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true,
      viewport: {width: 1440, height: 1100}, locale: 'zh-CN', env: {...process.env, TEMP: temp, TMP: temp, TMPDIR: temp}, args: ['--no-first-run', '--no-default-browser-check']});
    report.browser = context.browser()?.version();
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.protocol === 'blob:') return route.continue();
      const allowed = ['GET', 'HEAD'].includes(request.method()) || request.method() === 'POST' && ['/api/references/upload', '/api/models/' + testModelId + '/reference', '/api/models/' + testModelId + '/enable', '/api/models/disable', originalSelection?.active_model ? '/api/models/' + originalSelection.active_model + '/enable' : ''].includes(url.pathname);
      if (url.origin !== base || !allowed) {report.blockedRequests.push({url: request.url(), method: request.method()}); return route.abort('blockedbyclient');}
      if (request.method() === 'POST') report.mutations.push({path: url.pathname, bytes: request.postDataBuffer()?.length ?? 0});
      return route.continue();
    });
    page = context.pages()[0]; page.setDefaultTimeout(30000);
    page.on('console', item => {if (item.type() === 'error') report.consoleErrors.push(item.text());});
    page.on('pageerror', error => report.pageErrors.push(error.stack ?? String(error)));
    page.on('response', response => {if (response.status() >= 400) report.httpErrors.push({status: response.status(), url: response.url()});});
    page.on('requestfailed', request => report.requestFailures.push({url: request.url(), error: request.failure()?.errorText}));
    const models = await get('/models'), comparisons = await get('/comparisons'), beforeJobs = await get('/jobs');
    originalSelection = await get('/synthesis/selection');
    const beforeReferences = await get('/references');
    const selectedComparison = process.env.DICTIONFORGE_BROWSER_COMPARISON ? comparisons.find(item => item.id === process.env.DICTIONFORGE_BROWSER_COMPARISON) : comparisons.find(item => item.state === 'COMPLETED');
    assert.ok(selectedComparison, '需要已有的真实双模型完成比较');
    const datasets = await get('/datasets');
    const sourceVersion = datasets.find(version => version.id === '76b5a08af4ca471f9c6fa9c903347e69');
    const source = sourceVersion?.clips.find(clip => clip.seconds >= 3 && clip.seconds <= 10 && clip.text?.trim());
    assert.ok(source, '需要已有有效参考音频');
    const sourcePath = path.resolve(source.path);
    assert.ok(sourcePath.toLowerCase().startsWith(path.join(root, 'data').toLowerCase() + path.sep), '仅复制本项目已管理参考');
    const input = path.join(runDir, 'reference-upload.wav'); fs.copyFileSync(sourcePath, input);
    const sourceSha = sha(sourcePath);
    report.before = {selection: originalSelection.active_model, jobIds: beforeJobs.map(job => job.id).sort(), referenceIds: beforeReferences.map(ref => ref.id).sort(), source: {path: sourcePath, sha256: sourceSha}};
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    await page.getByRole('navigation', {name: '主工作区'}).getByRole('button', {name: '02语音合成'}).click();
    const panel = page.locator('.synthesis-panel');
    await panel.getByRole('heading', {name: '音色与效果试听'}).waitFor();
    await panel.getByLabel('选择模型版本', {exact: true}).selectOption(testModelId);
    await panel.locator('.reference-editor > summary').click();
    const uploadButton = panel.getByRole('button', {name: '上传并设为默认参考'});
    assert.equal(await uploadButton.isDisabled(), true);
    await panel.getByLabel('参考音频文件', {exact: true}).setInputFiles(input);
    assert.equal(await uploadButton.isDisabled(), true, '没有准确文本不能上传');
    await panel.getByLabel('参考音频准确文本', {exact: true}).fill(source.text);
    const uploadedResponse = page.waitForResponse(response => response.url().startsWith(base + '/api/references/upload?') && response.status() === 200);
    await uploadButton.click();
    await uploadedResponse;
    await panel.getByText('默认参考已更新，后续试听将使用新素材与文本。', {exact: true}).waitFor();
    // Newer system Edge may evict the raw-upload response from its inspector
    // cache. Verify the actual committed record through the read API instead.
    const uploaded = (await get('/references')).find(reference => !beforeReferences.some(previous => previous.id === reference.id));
    assert.ok(uploaded, '成功提示后必须存在新的已登记参考');
    report.uploadedReference = uploaded;
    assert.equal(uploaded.sample_rate, 48000); assert.ok(uploaded.seconds >= 3 && uploaded.seconds <= 10);
    await panel.getByLabel('已管理参考', {exact: true}).selectOption(uploaded.id);
    await panel.getByRole('button', {name: '设为默认参考', exact: true}).click();
    await panel.getByText('默认参考已更新，后续试听将使用新素材与文本。', {exact: true}).waitFor();
    pass('真实文件上传、文本门控与已管理参考选择', {referenceId: uploaded.id, bytes: fs.statSync(input).size, seconds: uploaded.seconds, sample_rate: uploaded.sample_rate});
    const referencePlayback = await playback(panel.locator(':scope > .reference-preview audio'));
    assert.ok(referencePlayback.currentTime > 0 && referencePlayback.events.includes('playing'));
    pass('默认参考预览与播放事件', referencePlayback);
    await screenshot('01-reference-upload', panel.locator('.reference-editor'));
    const disable = panel.getByRole('button', {name: '停用模型', exact: true});
    if (await disable.isEnabled()) {await disable.click(); await panel.getByText('已停用模型选择。', {exact: true}).waitFor();}
    await panel.getByRole('button', {name: '启用此模型', exact: true}).click();
    await panel.getByRole('button', {name: '已启用当前模型', exact: true}).waitFor();
    assert.equal((await get('/synthesis/selection')).active_model, testModelId);
    assert.match(await panel.locator('.synthesis-status').innerText(), /逻辑启用/);
    assert.match(await panel.locator('.synthesis-status').innerText(), /GPU 实际任务/);
    pass('逻辑启用与实际 GPU 状态独立', await get('/synthesis/selection'));
    await panel.locator('.reference-editor > summary').click();
    await screenshot('02-model-enabled', panel);

    const currentModel = (await get('/models')).find(model => model.id === selectedComparison.model_id);
    const oldComparison = comparisons.find(item => item.model_id === selectedComparison.model_id && item.state === 'COMPLETED' && item.reference?.fingerprint !== currentModel?.default_reference?.fingerprint);
    if (oldComparison) {
      await panel.getByLabel('历史对比试听', {exact: true}).selectOption(oldComparison.id);
      await panel.locator('.comparison-results').getByText('此来源模型的默认参考已更换。以下保留历史比较结果；再次生成将使用新参考条件。', {exact: true}).waitFor();
      pass('历史参考变更提示绑定比较来源模型', {oldComparisonId: oldComparison.id, sourceModel: selectedComparison.model_id});
    }
    await panel.getByLabel('历史对比试听', {exact: true}).selectOption(selectedComparison.id);
    await panel.locator('.comparison-results audio').nth(1).waitFor();
    const details = await get('/comparisons/' + selectedComparison.id);
    assert.equal(details.state, 'COMPLETED'); assert.equal(details.jobs.length, 2);
    const fingerprints = details.jobs.map(job => job.result.conditions_fingerprint);
    assert.equal(fingerprints[0], fingerprints[1]);
    if (details.reference?.fingerprint === currentModel?.default_reference?.fingerprint) assert.equal(await panel.locator('.comparison-results .notice').count(), 0, '查看其他模型不能误报本比较参考已改变');
    assert.equal(details.parameters.seed, 42); assert.equal(details.parameters.sample_steps, 32);
    const played = [];
    for (let index = 0; index < 2; index++) {
      const item = await playback(panel.locator('.comparison-results audio').nth(index));
      assert.ok(item.currentTime > 0 && item.events.includes('playing'));
      assert.equal(details.jobs[index].result.audio.sample_rate, 48000);
      played.push({...item, jobId: details.jobs[index].id, model: details.jobs[index].payload.model_version_id, audio: details.jobs[index].result.audio});
    }
    pass('已有自训与官方同条件真实结果可播放', {comparisonId: details.id, conditions_fingerprint: fingerprints[0], parameters: details.parameters, played});
    await screenshot('03-comparison-results', panel.locator('.comparison-results'));
    await page.setViewportSize({width: 390, height: 844});
    await panel.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, document.querySelector('.synthesis-panel').getBoundingClientRect().top + window.scrollY));
    await screenshot('04-narrow-synthesis');
    const dimensions = await page.evaluate(() => ({viewport: window.innerWidth, scroll: document.documentElement.scrollWidth}));
    assert.equal(dimensions.viewport, dimensions.scroll);
    pass('窄屏参考与试听布局', dimensions);

    await disable.click(); await panel.getByText('已停用模型选择。', {exact: true}).waitFor();
    if (originalSelection.enabled && originalSelection.active_model) {
      await panel.getByLabel('选择模型版本', {exact: true}).selectOption(originalSelection.active_model);
      await panel.getByRole('button', {name: '启用此模型', exact: true}).click();
      await panel.getByRole('button', {name: '已启用当前模型', exact: true}).waitFor();
    }
    const afterJobs = await get('/jobs'), afterReferences = await get('/references'), afterSelection = await get('/synthesis/selection');
    report.after = {selection: afterSelection.active_model, jobIds: afterJobs.map(job => job.id).sort(), referenceIds: afterReferences.map(ref => ref.id).sort(), sourceSha256: sha(sourcePath)};
    assert.deepEqual(report.after.jobIds, report.before.jobIds); assert.equal(report.after.selection, report.before.selection); assert.equal(report.after.sourceSha256, sourceSha);
    assert.equal(afterReferences.length, beforeReferences.length + 1);
    assert.equal(report.blockedRequests.length, 0); assert.equal(report.consoleErrors.length, 0); assert.equal(report.pageErrors.length, 0); assert.equal(report.httpErrors.length, 0);
    pass('验收变更范围与浏览器错误', '新增1份独立参考；保留该测试模型默认参考；恢复原逻辑选择；无新GPU任务；源文件哈希不变；无console/page/HTTP错误。');
    report.status = 'PASS';
  } catch (error) {report.status = 'FAILED'; report.error = error.stack ?? String(error); process.exitCode = 1; if (page) await screenshot('failure').catch(() => {});}
  finally {
    if (context && originalSelection) {
      try {
        const current = await get('/synthesis/selection');
        if (current.active_model !== originalSelection.active_model) {
          const endpoint = originalSelection.active_model ? '/models/' + encodeURIComponent(originalSelection.active_model) + '/enable' : '/models/disable';
          const restored = await context.request.post(base + '/api' + endpoint, {data: {request_id: crypto.randomUUID()}});
          report.selectionCleanup = {status: restored.status(), original: originalSelection.active_model, restored: (await restored.json()).active_model};
          if (!restored.ok()) {report.status = 'FAILED'; process.exitCode = 1;}
        }
      } catch (error) {report.selectionCleanup = {error: String(error)}; report.status = 'FAILED'; process.exitCode = 1;}
    }
    if (context) await context.close();
    report.finished = new Date().toISOString();
    const json = JSON.stringify(report, null, 2); fs.writeFileSync(path.join(runDir, 'report.json'), json); fs.writeFileSync(path.join(output, 'latest.json'), json);
    console.log(JSON.stringify({status: report.status, checks: report.checks.length, error: report.error, report: path.join(runDir, 'report.json')}, null, 2));
  }
})();
