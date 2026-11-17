// Read-only P07 smoke against the existing local app. No model/recording mutation.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'data', 'p07-browser');
const runId = new Date().toISOString().replace(/[:.]/g, '-');
const runDir = path.join(output, runId);
fs.mkdirSync(runDir, {recursive: true});
const runtimeModule = path.resolve(path.dirname(process.execPath), '../node_modules/playwright');
const {chromium} = require(runtimeModule);
const executablePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const base = 'http://127.0.0.1:7867';
const report = {stage: 'P07', started: new Date().toISOString(), base, runDir,
  playwright: require(path.join(runtimeModule, 'package.json')).version, executablePath,
  headless: true, scope: 'Existing UI only; no training, synthesis, import, save, or deletion submitted.',
  checks: [], consoleErrors: [], pageErrors: [], httpErrors: [], requestFailures: [], blockedMutations: [], screenshots: []};
let context;
let page;
const check = (name, detail) => report.checks.push({name, status: 'PASS', detail});
const screenshot = async (name, locator) => {
  const file = path.join(runDir, name + '.png');
  if (locator) await locator.screenshot({path: file});
  else await page.screenshot({path: file, fullPage: false});
  report.screenshots.push({path: file, sha256: crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')});
};
const get = async endpoint => {
  const response = await context.request.get(base + '/api' + endpoint);
  assert.equal(response.status(), 200, endpoint);
  return response.json();
};
const stateIds = value => value.map(row => row.id).sort();

(async () => {
  try {
    const temp = path.join(runDir, 'temp');
    fs.mkdirSync(temp);
    context = await chromium.launchPersistentContext(path.join(runDir, 'profile'), {
      executablePath, headless: true, viewport: {width: 1440, height: 1100}, locale: 'zh-CN',
      downloadsPath: path.join(runDir, 'downloads'),
      env: {...process.env, TEMP: temp, TMP: temp, TMPDIR: temp},
      args: ['--no-first-run', '--no-default-browser-check'],
    });
    report.browserVersion = context.browser()?.version() ?? 'Edge persistent context';
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base || !['GET', 'HEAD'].includes(request.method())) {
        report.blockedMutations.push({url: request.url(), method: request.method()});
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    page = context.pages()[0] ?? await context.newPage();
    page.setDefaultTimeout(20000);
    page.on('console', message => {if (message.type() === 'error') report.consoleErrors.push(message.text());});
    page.on('pageerror', error => report.pageErrors.push(error.stack ?? String(error)));
    page.on('response', response => {if (response.status() >= 400) report.httpErrors.push({status: response.status(), url: response.url()});});
    page.on('requestfailed', request => report.requestFailures.push({url: request.url(), error: request.failure()?.errorText}));
    const before = {models: await get('/models'), datasets: await get('/datasets'), jobs: await get('/jobs')};
    report.before = {modelIds: stateIds(before.models), datasetIds: stateIds(before.datasets), jobIds: stateIds(before.jobs)};
    await page.goto(base, {waitUntil: 'domcontentloaded'});
    await page.getByText('本地服务已连接', {exact: false}).waitFor();
    const nav = page.getByRole('navigation', {name: '主工作区'});
    await nav.getByRole('button', {name: '02语音合成'}).click();
    await page.getByRole('heading', {name: '把文字变成声音'}).waitFor();
    await nav.getByRole('button', {name: '01微调训练'}).click();
    await page.getByRole('heading', {name: '训练配置与预检'}).waitFor();
    check('中文双标签切换', '语音合成与微调训练标签均由真实 DOM 点击切换。');
    await screenshot('01-training-workspace');

    const catalog = page.locator('.model-management');
    await catalog.getByRole('button', {name: '刷新模型与版本'}).click();
    await catalog.locator('.model-card').first().waitFor();
    assert.equal(await catalog.locator('.model-card').count(), before.models.length);
    const modelDetails = [];
    for (const model of before.models) {
      const card = catalog.locator('.model-card').filter({hasText: '版本 ' + model.id}).first();
      await card.waitFor();
      const text = await card.innerText();
      assert.match(text, /结构检查\s+通过/);
      assert.match(text, model.validation.gpu === 'PASS' ? /GPU 试听\s+通过/ : /GPU 试听\s+尚未运行/);
      if (model.id === 'official') assert.equal(await card.getByRole('button', {name: '查看删除范围'}).count(), 0);
      modelDetails.push({id: model.id, validation: model.validation, text});
    }
    check('模型卡真实校验状态与官方底模禁删', modelDetails);
    await screenshot('02-model-cards', catalog);

    const selectedModel = before.models.find(model => model.id !== 'official');
    assert.ok(selectedModel, '需要一个已发布自定义模型检查删除预览');
    const modelCard = catalog.locator('.model-card').filter({hasText: '版本 ' + selectedModel.id}).first();
    await modelCard.getByRole('button', {name: '查看删除范围'}).click();
    const confirmation = catalog.getByRole('region', {name: '删除确认'});
    await confirmation.waitFor();
    assert.ok(await confirmation.locator('.delete-files p').count() >= 4);
    const modelPreview = await confirmation.innerText();
    await screenshot('03-model-delete-preview', confirmation);
    await confirmation.getByRole('button', {name: '取消', exact: true}).click();
    await confirmation.waitFor({state: 'detached'});
    check('模型删除预览与取消', {modelId: selectedModel.id, text: modelPreview, deleteSubmitted: false});

    const datasetSection = catalog.locator('details').filter({has: page.locator('summary').filter({hasText: '已发布数据版本'})});
    await datasetSection.locator('summary').click();
    await datasetSection.locator('.version-row').first().getByRole('button', {name: '查看删除范围'}).click();
    await confirmation.waitFor();
    const datasetPreview = await confirmation.innerText();
    assert.match(datasetPreview, /受到引用保护/);
    assert.equal(await confirmation.getByRole('button', {name: '确认删除此数据版本'}).isDisabled(), true);
    await screenshot('04-protected-dataset-preview', confirmation);
    await confirmation.getByRole('button', {name: '取消', exact: true}).click();
    check('数据删除引用保护与取消', {text: datasetPreview, deleteSubmitted: false});

    const training = [];
    for (const job of before.jobs.filter(job => job.kind === 'train')) {
      const snapshot = await get('/training/' + job.id);
      if (snapshot.exports?.length) training.push({job, snapshot});
    }
    assert.ok(training.length, '需要已存在的导出运行');
    const selectedRun = training[0];
    const pointsResponse = page.waitForResponse(response => response.url() === base + '/api/training/' + selectedRun.job.id + '/checkpoints' && response.status() === 200);
    await page.locator('.job-row').filter({hasText: selectedRun.job.id.slice(0, 8)}).click();
    const runPanel = page.locator('.training-run');
    await runPanel.getByText('模型已发布', {exact: true}).first().waitFor();
    const points = await (await pointsResponse).json();
    await runPanel.getByText('训练恢复点 · ' + points.checkpoints.length + ' 个', {exact: true}).waitFor();
    assert.equal(await runPanel.locator('.run-stage').count(), 2);
    assert.ok(await runPanel.getByRole('button', {name: '导出已保存权重对'}).count());
    const runText = await runPanel.innerText();
    await screenshot('05-training-export-status', runPanel);
    check('既有训练阶段与独立导出状态', {runId: selectedRun.job.id, state: selectedRun.job.state, exportIds: selectedRun.snapshot.exports.map(job => job.id), text: runText});
    const failedExport = before.jobs.find(job => job.kind === 'model_export' && job.state === 'FAILED');
    if (failedExport) {
      await page.locator('.job-row').filter({hasText: failedExport.id.slice(0, 8)}).click();
      await page.locator('.task-detail').getByRole('button', {name: '从同一快照重试导出'}).waitFor();
      check('失败导出提供原快照重试入口', {jobId: failedExport.id, clicked: false});
    }

    const drafts = await get('/dataset-drafts');
    const publishedDraft = drafts.find(draft => draft.published);
    assert.ok(publishedDraft, '需要已发布草稿进行现有音频加载检查');
    await page.locator('label').filter({hasText: /^选择草稿/}).locator('select').selectOption(publishedDraft.id);
    const audio = page.locator('.review-clip audio').first();
    await audio.waitFor();
    const playback = await audio.evaluate(async element => {
      const events = [];
      for (const name of ['loadedmetadata', 'canplay', 'play', 'playing', 'pause', 'error']) element.addEventListener(name, () => events.push(name));
      element.muted = true;
      await Promise.race([element.play(), new Promise((_, reject) => setTimeout(() => reject(new Error('audio play timeout')), 15000))]);
      await new Promise(resolve => setTimeout(resolve, 350));
      element.pause();
      return {src: element.currentSrc, duration: element.duration, currentTime: element.currentTime, readyState: element.readyState, events, muted: element.muted};
    });
    assert.ok(playback.duration > 0 && playback.currentTime > 0);
    assert.ok(playback.events.includes('playing'));
    check('既有草稿音频浏览器解码与播放事件', {...playback, subjectiveListening: 'NOT_PERFORMED'});

    await page.setViewportSize({width: 390, height: 844});
    await nav.getByRole('button', {name: '02语音合成'}).click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await screenshot('06-narrow-layout');
    const dimensions = await page.evaluate(() => ({viewport: window.innerWidth, scroll: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll('body *')].filter(element => element.getBoundingClientRect().right > window.innerWidth + 1 || element.scrollWidth > element.clientWidth + 1).slice(-24).map(element => ({tag: element.tagName, className: element.className, right: element.getBoundingClientRect().right, width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth, text: element.textContent.slice(0, 180)}))}));
    report.narrowLayout = dimensions;
    assert.ok(dimensions.scroll <= dimensions.viewport, '窄屏不能横向溢出');
    check('390px 窄屏布局', dimensions);

    const after = {models: await get('/models'), datasets: await get('/datasets'), jobs: await get('/jobs')};
    report.after = {modelIds: stateIds(after.models), datasetIds: stateIds(after.datasets), jobIds: stateIds(after.jobs)};
    assert.deepEqual(report.after, report.before, '未新增任务、模型或数据版本');
    assert.equal(report.blockedMutations.length, 0);
    assert.equal(report.pageErrors.length, 0);
    assert.equal(report.consoleErrors.length, 0);
    assert.equal(report.httpErrors.length, 0);
    check('浏览器异常与应用数据范围', '无 pageerror / console.error / HTTP >=400；无非 GET/HEAD 请求；模型、数据与任务 ID 集合不变。');
    report.status = 'PASS';
  } catch (error) {
    report.status = 'FAILED';
    report.error = error.stack ?? String(error);
    if (page) await screenshot('failure').catch(() => {});
    process.exitCode = 1;
  } finally {
    if (context) await context.close();
    report.finished = new Date().toISOString();
    const json = JSON.stringify(report, null, 2);
    fs.writeFileSync(path.join(runDir, 'report.json'), json);
    fs.writeFileSync(path.join(output, 'latest.json'), json);
    console.log(JSON.stringify({status: report.status, checks: report.checks.length, error: report.error, report: path.join(runDir, 'report.json'), screenshots: report.screenshots.map(item => item.path)}, null, 2));
  }
})();
