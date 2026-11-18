// UI regression: real local catalogs, plus explicitly marked browser-only
// fixtures for long drafts/results. No training, publishing, or deletion occurs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const {chromium} = require(path.resolve(path.dirname(process.execPath), '../node_modules/playwright'));
const root = path.resolve(__dirname, '..'), base = 'http://127.0.0.1:7867';
const runDir = path.join(root, 'data/ui-pagination', new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(runDir, {recursive:true});
const report = {started:new Date().toISOString(), checks:[], errors:[], runDir, fixtures:[], writes:[]};
let context, page;
const check = (name, details = {}) => report.checks.push({name, status:'PASS', ...details});
const group = label => page.getByRole('group', {name:label + '分页', exact:true});
const next = label => group(label).getByRole('button', {name:'下一页', exact:true});
const previous = label => group(label).getByRole('button', {name:'上一页', exact:true});
async function until(fn) {for (let i=0;i<100;i++) {if (await fn()) return; await new Promise(r=>setTimeout(r,100));} throw Error('condition timeout');}
async function get(route) {const r = await context.request.get(base + '/api' + route); assert.ok(r.ok(), await r.text()); return r.json();}
async function count(locator, n) {await until(async()=>await locator.count() === n);}
async function flip(label, rows, expectedLast) {
  await count(rows,5); const first = await rows.first().textContent();
  await next(label).click(); await count(rows,expectedLast);
  assert.notEqual(await rows.first().textContent(),first);
  await previous(label).click(); await count(rows,5);
  assert.equal(await rows.first().textContent(),first);
  check(label + '每页5项且前后翻页', {secondPage:expectedLast});
}
async function expand(summary) {const target=page.locator('summary').filter({hasText:summary}).first(); await target.evaluate(el=>el.parentElement.open=true);}
async function realCatalogs() {
  await page.goto(base);
  await count(page.locator('.job-row'),5);
  await page.locator('.job-row').first().click();
  const selected=await page.locator('.job-row.selected').innerText();
  await next('后台任务').click(); await count(page.locator('.job-row'),5);
  const second=await page.locator('.job-row').first().innerText();
  // Refresh runs the same path as periodic polling without waiting for a timer.
  await page.getByRole('button',{name:'刷新状态',exact:true}).click();
  await until(async()=>await group('后台任务').innerText().then(s=>s.includes('第 2 /')));
  assert.equal(await page.locator('.job-row').first().innerText(),second);
  await previous('后台任务').click(); assert.equal(await page.locator('.job-row.selected').innerText(),selected);
  check('后台任务刷新与翻页保留选中任务');
  await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();
  await flip('模型版本',page.locator('.model-card'),Math.min(5,(await get('/models')).length-5));

  await expand('已发布数据版本');
  await flip('管理数据版本',page.locator('.model-management > details:not(.draft-management) > .version-row'),5);
  await flip('管理标注草稿',page.locator('.draft-management > .version-row'),5);
  await page.getByRole('button',{name:'01微调训练',exact:true}).click();
  await flip('数据版本',page.locator('.version-list > .version-row'),5);
  await flip('预训练资产',page.locator('.asset-row'),5);
  const jobs=await get('/jobs'), scan=jobs.find(j=>j.kind==='dataset_scan'&&j.result?.files?.length>5);
  assert.ok(scan); await page.getByLabel('扫描记录',{exact:true}).selectOption(scan.id);
  await count(page.locator('.scan-file'),5);
  const box=page.locator('.scan-file').first().getByRole('checkbox',{name:'排除',exact:true}); await box.check();
  await next('扫描文件').click(); await count(page.locator('.scan-file'),scan.result.files.length-5);
  await previous('扫描文件').click(); assert.ok(await box.isChecked()); await box.uncheck();
  check('扫描文件跨页保留排除勾选',{scanId:scan.id,total:scan.result.files.length});
  const drafts=await get('/dataset-drafts'), draft=drafts.find(d=>d.clips.length>5);
  await page.getByLabel('选择草稿',{exact:true}).selectOption(draft.id);
  await expand('全部标注与审核状态');
  await flip('标注总览',page.locator('.review-overview .review-summary'),draft.clips.length-5);
  const saved=await get('/saved-speech?page_size=5'); assert.ok(saved.total>5);
  await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();
  const library=page.getByRole('region',{name:'已保存语音',exact:true});
  await flip('已保存语音',library.locator('.saved-speech-card'),saved.total-5);
  await next('已保存语音').click();
  await library.getByLabel('作品关键词筛选',{exact:true}).fill(saved.items[0].title);
  await until(async()=>await group('已保存语音').innerText().then(s=>s.includes('第 1 /')));
  assert.ok(await previous('已保存语音').isDisabled());
  await library.getByLabel('作品关键词筛选',{exact:true}).fill(''); await count(library.locator('.saved-speech-card'),5);
  await library.getByRole('button',{name:/预览删除当前筛选全部/}).click();
  await count(page.locator('[data-preview-id]'),5);
  const preview=page.getByRole('region',{name:'作品删除确认'});
  assert.ok((await preview.innerText()).includes('确认删除 '+saved.total+' 件作品'));
  await next('待删除作品').click(); await count(page.locator('[data-preview-id]'),saved.total-5);
  assert.ok((await preview.innerText()).includes('确认删除这 '+saved.total+' 件作品'));
  await preview.getByRole('button',{name:'取消删除',exact:true}).click();
  check('作品筛选回首页与跨页冻结删除预览',{total:saved.total,deleted:0});
  await page.screenshot({path:path.join(runDir,'desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await group('已保存语音').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(runDir,'mobile.png')});
  check('390px窄屏分页无横向溢出');
  await page.setViewportSize({width:1440,height:1050});
  return {draft, saved, jobs};
}
async function draftFixture(source) {
  const draft=structuredClone(source); delete draft.published;
  draft.clips=Array.from({length:12},(_,i)=>({...source.clips[i%source.clips.length],id:'ui-clip-'+i,reviewed:false,excluded:false}));
  report.fixtures.push('仅浏览器响应：12段可编辑草稿，PATCH只更新测试内存，服务端原草稿不变');
  const commits=[];
  await page.route('**/api/dataset-drafts/'+draft.id,async route=>{
    if(route.request().method()==='PATCH') {
      const body=route.request().postDataJSON();assert.equal(body.revision,draft.revision);commits.push(body);
      for(const change of body.clips) Object.assign(draft.clips.find(c=>c.id===change.id),change);
      draft.revision++;
    }
    await route.fulfill({json:draft});
  });
  await page.getByRole('button',{name:'01微调训练',exact:true}).click();
  await page.getByLabel('选择草稿',{exact:true}).selectOption('');
  await page.getByLabel('选择草稿',{exact:true}).selectOption(draft.id);
  const review=page.getByRole('region',{name:'文字审核窗口',exact:true});
  await review.getByRole('button',{name:'开始逐条审核',exact:true}).click();
  await review.getByRole('textbox',{name:'审核文案',exact:true}).fill('审核修正后的文案。');
  await review.getByRole('button',{name:'通过，下一条',exact:true}).click();
  await until(async()=>await review.locator('[data-review-id]').getAttribute('data-review-id')==='ui-clip-1');
  assert.equal(draft.clips[0].text,'审核修正后的文案。');assert.equal(draft.clips[0].reviewed,true);
  await review.getByRole('button',{name:'去掉数据，下一条',exact:true}).click();
  await until(async()=>await review.locator('[data-review-id]').getAttribute('data-review-id')==='ui-clip-2');
  assert.equal(draft.clips[1].excluded,true);
  await review.getByRole('button',{name:'结束审核，剩余默认通过',exact:true}).click();
  await count(review.locator('[data-review-id]'),0);
  assert.ok(draft.clips.every(c=>c.excluded||c.reviewed));assert.equal(commits.length,3);
  check('逐条审核修正文案、通过或去掉即保存跳转、中途结束保留排除并默认通过剩余');
  for(const clip of draft.clips) {clip.reviewed=false;clip.excluded=false;}
  await page.getByLabel('选择草稿',{exact:true}).selectOption('');await page.getByLabel('选择草稿',{exact:true}).selectOption(draft.id);
  await review.getByRole('button',{name:'跳过审核，全部默认通过',exact:true}).click();
  await until(()=>draft.clips.every(c=>c.reviewed));assert.equal(commits.length,4);
  check('跳过整个审核将全部保留片段默认通过并保存');
  draft.clips[0].text='';draft.clips[0].reviewed=false;
  await page.getByLabel('选择草稿',{exact:true}).selectOption('');await page.getByLabel('选择草稿',{exact:true}).selectOption(draft.id);
  await review.getByRole('button',{name:'跳过审核，全部默认通过',exact:true}).click();
  await until(async()=>await review.getByRole('alert').count()===1);
  assert.equal(commits.length,4);assert.ok(await review.getByRole('button',{name:'通过，下一条',exact:true}).isDisabled());
  check('识别为空时定位该片段，不伪造可训练的空标注');
  await page.getByLabel('选择草稿',{exact:true}).selectOption('');await page.unroute('**/api/dataset-drafts/'+draft.id);
  assert.deepEqual(await get('/dataset-drafts/'+source.id),source);check('审核测试未改动服务端原草稿');
}
async function speechFixtures() {
  const snapshot=await get('/tts/current'); assert.ok(snapshot?.result?.audio?.audio_id);
  const original=structuredClone(snapshot), audio=snapshot.result.audio, template=snapshot.result.sentences[0];
  const text='分页试听。'.repeat(12), stride=Math.floor(audio.samples/12);
  snapshot.request.text=text;
  snapshot.result.sentences=Array.from({length:12},(_,i)=>({...template,sentence_id:'ui-sentence-'+i,index:i,text:'分页试听。',source_start:i*5,source_end:(i+1)*5,start_sample:i*stride,end_sample:i===11?audio.samples:(i+1)*stride}));
  const rows=Array.from({length:6},(_,i)=>({id:'ui-append-'+i,state:'REVIEW_REQUIRED',asr_calls:6,job:{id:'ui-job-'+i,state:'COMPLETED'},
    draft:{id:'ui-draft-'+i,revision:1,clips:Array.from({length:6},(_,j)=>({id:'ui-append-clip-'+i+'-'+j,audio_id:audio.audio_id,text:'回流标注 '+i+' '+j,reviewed:false,excluded:false,seconds:1,label_source:'asr'})),
      rejected:Array.from({length:6},(_,j)=>({start_sample:j*48000,end_sample:(j+1)*48000,reason:'浏览器分页夹具'}))}}));
  report.fixtures.push('仅浏览器响应：12句索引、6条回流记录各6片、6个未保留区间；媒体仍为既有4秒音频，未生成或回流新数据');
  await page.route('**/api/tts/current',route=>route.fulfill({json:snapshot}));
  await page.route('**/api/augmentations?*',route=>route.fulfill({json:rows}));
  await page.route('**/api/dataset-drafts/ui-draft-*',async route=>{
    const request=route.request(); assert.equal(request.method(),'PATCH');
    const row=rows.find(r=>request.url().endsWith(r.draft.id)); assert.ok(row);
    for(const change of request.postDataJSON().clips) Object.assign(row.draft.clips.find(c=>c.id===change.id),change);
    row.draft.revision++; await route.fulfill({json:row.draft});
  });
  await page.reload();
  await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();
  const playback=page.locator('.speech-playback'), sentences=playback.locator('.speech-sentence');
  await count(sentences,5); await next('合成句子').click(); await count(sentences,5);
  const player=playback.getByLabel('完整合成音频',{exact:true});
  await player.evaluate(async a=>{a.muted=true;await a.play();a.pause();});
  await playback.getByRole('button',{name:'选择第 6 句并定位',exact:true}).click();
  await until(async()=>await playback.locator('.speech-sentence.is-current').getAttribute('data-sentence-id')==='ui-sentence-5');
  await until(async()=>await player.evaluate((a,target)=>Math.abs(a.currentTime-target)<.05,5*stride/48000));
  await previous('合成句子').click();
  await player.evaluate(a=>{a.currentTime=3.76;a.dispatchEvent(new Event('seeked'));});
  await until(async()=>await group('合成句子').innerText().then(s=>s.includes('第 3 /')));
  await count(sentences,2);
  assert.equal(await playback.locator('.speech-sentence.is-current').getAttribute('data-sentence-id'),'ui-sentence-11');
  check('句子分页保留选择和真实播放器定位、拖动自动显示当前句');
  const append=page.locator('.append-sentence');
  // The class is checked below from the label so fixture tests also verify accessibility.
  const first=page.locator('.feature-card').filter({hasText:'ui-appen'}).first();
  await count(page.locator('.feature-card').filter({hasText:'ui-appen'}),5);
  const clips=first.locator('.review-clip'); await count(clips,5);
  await clips.first().getByRole('textbox').fill('保留第一条的改字');
  await first.getByRole('group',{name:'回流片段 ui-appen分页',exact:true}).getByRole('button',{name:'下一页'}).click();
  await count(clips,1); await clips.first().getByRole('textbox').fill('保留第六片的改字');
  await next('回流记录').click(); await count(page.locator('.feature-card').filter({hasText:'ui-appen'}),1);
  await first.locator('.review-clip').first().getByRole('textbox').fill('保留第六条的改字');
  // Saving this row uses a browser-only PATCH. Other rows' unsaved edits remain.
  await first.getByRole('button',{name:'保存回流校对',exact:true}).click();
  await until(async()=>rows[5].draft.revision===2);
  await previous('回流记录').click(); await count(page.locator('.feature-card').filter({hasText:'ui-appen'}),5);
  assert.equal(await first.locator('.review-clip').first().getByRole('textbox').inputValue(),'保留第一条的改字');
  await first.getByRole('group',{name:'回流片段 ui-appen分页',exact:true}).getByRole('button',{name:'下一页'}).click();
  assert.equal(await first.locator('.review-clip').first().getByRole('textbox').inputValue(),'保留第六片的改字');
  await first.locator('summary').filter({hasText:'切分规则未保留的区间'}).click();
  const rejected=first.getByRole('group',{name:'未保留区间 ui-appen分页',exact:true});
  await rejected.getByRole('button',{name:'下一页'}).click();
  assert.ok(await rejected.getByRole('button',{name:'下一页'}).isDisabled());
  check('嵌套回流列表和片段每页5项、跨页编辑及保存单条不清空其他记录');
  assert.deepEqual(await get('/tts/current'),original);
}
async function trainingFixtures() {
  const job=(await get('/jobs')).find(j=>j.kind==='train'&&j.state==='COMPLETED');assert.ok(job);
  const view=await get('/training/'+job.id),points=await get('/training/'+job.id+'/checkpoints');
  assert.ok(points.checkpoints.length&&view.exports.length);
  const longPoints={...points,checkpoints:Array.from({length:6},(_,i)=>({...points.checkpoints[0],id:'ui-checkpoint-'+i}))};
  const longView={...view,exports:Array.from({length:6},(_,i)=>({...view.exports[0],id:'ui-export-'+i})),
    export_intents:Array.from({length:6},(_,i)=>({intent_id:'ui-intent-'+i,state:'FAILED',error:'仅用于分页展示的夹具'}))};
  report.fixtures.push('仅浏览器响应：6个恢复点、6项导出和6项导出登记；未调用恢复、导出或训练');
  await page.route('**/api/training/'+job.id+'/checkpoints',r=>r.fulfill({json:longPoints}));
  await page.route('**/api/training/'+job.id,r=>r.fulfill({json:longView}));
  await page.evaluate(id=>localStorage.setItem('dictionforge.selectedJob',id),job.id);await page.reload();
  const pointsList=page.locator('.checkpoint-list').filter({has:group('训练恢复点')});
  await flip('训练恢复点',pointsList.locator('.checkpoint-row'),1);
  const exportsList=page.locator('.checkpoint-list').filter({has:group('导出记录')});
  await flip('导出记录',exportsList.locator('.checkpoint-row'),1);
  const intentRows=page.locator('.checkpoint-row').filter({hasText:'ui-intent-'});
  await flip('导出登记',intentRows,1);
}
(async()=>{try {
  const temp=path.join(runDir,'temp');fs.mkdirSync(temp);
  context=await chromium.launchPersistentContext(path.join(runDir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});
  page=await context.newPage(); page.on('pageerror',e=>report.errors.push(String(e))); page.on('console',m=>{if(m.type()==='error')report.errors.push(m.text());});page.on('dialog',d=>d.accept());
  // All business mutations are blocked. Only media leases and a frozen delete
  // preview can reach the live API; fixture PATCHes are fulfilled above.
  await page.route('**/api/**',async route=>{const req=route.request();if(!['GET','HEAD'].includes(req.method())) {report.writes.push({method:req.method(),url:req.url()});if(!/\/media-leases(?:\/|$)|\/saved-speech\/delete-preview$/.test(req.url())) {report.errors.push('blocked unexpected mutation '+req.url()); await route.abort(); return;}} await route.continue();});
  const {draft}=await realCatalogs(); await draftFixture(draft); await speechFixtures(); await trainingFixtures();
  assert.deepEqual(report.errors,[]);report.status='PASS';
} catch(e) {report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(runDir,'failure.png')}).catch(()=>{});
} finally {if(context)await context.close();report.finished=new Date().toISOString();fs.writeFileSync(path.join(runDir,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(root,'data/ui-pagination.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error,runDir}));}})();
