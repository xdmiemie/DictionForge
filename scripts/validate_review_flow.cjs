// Real pure-audio ASR/review/publication/deletion acceptance. All mutations use
// UI controls and are restricted to newly created, clearly named test records.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:7867';
const stamp=new Date().toISOString().replace(/[:.]/g,'-'),dir=path.join(root,'data/ui-review',stamp),input=path.join(dir,'纯语音输入');
fs.mkdirSync(input,{recursive:true});
const source=path.join(root,'data/fixtures/景元 训练/archive_jingyuan_1.wav');
const copied=path.join(input,'景元录音.wav');fs.copyFileSync(source,copied);
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const report={started:new Date().toISOString(),role:'UI审核删除验收 '+stamp,source,sourceSha:sha(source),input,checks:[],errors:[]};
let context,page;
function save(){fs.writeFileSync(path.join(dir,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(root,'data/ui-review.json'),JSON.stringify(report,null,2));}
function check(name){report.checks.push({name,status:'PASS'});console.log(name);save();}
async function get(route){const r=await context.request.get(base+'/api'+route);assert.ok(r.ok(),await r.text());return r.json();}
async function until(fn){const limit=Date.now()+300000;while(Date.now()<limit){if(await fn())return;await new Promise(r=>setTimeout(r,300));}throw Error('timeout');}
async function finished(id){let value;await until(async()=>{value=await get('/jobs/'+id);if(['RUNNING','QUEUED'].includes(value.state))return false;assert.equal(value.state,'COMPLETED',JSON.stringify(value));return true;});return value;}
async function click(button,route,method='POST'){const waiting=page.waitForResponse(r=>r.url().endsWith('/api'+route)&&r.request().method()===method,{timeout:60000});await button.click();const r=await waiting;assert.ok(r.ok(),await r.text());return r.json();}
(async()=>{try {
  const temp=path.join(dir,'temp');fs.mkdirSync(temp);
  context=await chromium.launchPersistentContext(path.join(dir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});
  page=await context.newPage();page.on('pageerror',e=>report.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.errors.push(m.text());});page.on('dialog',d=>d.accept());
  report.savedBefore=(await get('/saved-speech?page_size=10')).items.map(x=>({id:x.id,sha256:x.audio.sha256}));
  await page.goto(base);
  const panel=page.locator('.dataset-panel');
  await panel.getByPlaceholder('例如 E:\\角色录音\\中文素材').fill(input);
  await panel.getByPlaceholder('为这组声音起一个名字').fill(report.role);
  report.scan=await click(panel.getByRole('button',{name:'扫描录音',exact:true}),'/datasets/scan');save();report.scan=await finished(report.scan.id);
  await panel.getByLabel('扫描记录',{exact:true}).selectOption(report.scan.id);
  await panel.getByText('语音切分设置',{exact:true}).click();await panel.getByLabel('最短停顿（秒）',{exact:true}).fill('3');
  report.prepare=await click(panel.getByRole('button',{name:'整理数据集 · 切分并转写',exact:true}),'/datasets/prepare');save();report.prepare=await finished(report.prepare.id);save();
  const id=report.prepare.result.draft_id;
  report.asrDraft=await get('/dataset-drafts/'+id);
  assert.deepEqual(fs.readdirSync(input),['景元录音.wav']);
  assert.ok(report.asrDraft.clips.length&&report.asrDraft.clips.every(c=>c.label_source==='asr'&&c.text.trim()&&!c.asr_error));
  check('纯WAV无标注文件，真实STT生成可审核文字');
  await page.getByLabel('选择草稿',{exact:true}).selectOption(id);
  const review=page.getByRole('region',{name:'文字审核窗口',exact:true});
  await click(review.getByRole('button',{name:'跳过审核，全部默认通过',exact:true}),'/dataset-drafts/'+id,'PATCH');
  report.approved=await get('/dataset-drafts/'+id);assert.ok(report.approved.clips.every(c=>c.reviewed&&c.label_source==='asr'));
  check('跳过审核实际保存全部通过，保留STT标注来源');
  assert.equal(report.approved.clips.length,1);
  await review.getByRole('button',{name:'开始逐条审核',exact:true}).click();
  report.playback=await review.getByLabel('当前审核音频',{exact:true}).evaluate(async a=>{a.muted=true;await a.play();await new Promise(r=>setTimeout(r,200));a.pause();return {seconds:a.duration,playedTo:a.currentTime};});assert.ok(report.playback.playedTo>0);
  const corrected=fs.readFileSync(source.replace(/\.wav$/i,'.lab'),'utf8').replace(/^\uFEFF/,'').trim();
  await review.getByRole('textbox',{name:'审核文案',exact:true}).fill(corrected);
  await click(review.getByRole('button',{name:'通过，下一条',exact:true}),'/dataset-drafts/'+id,'PATCH');
  report.corrected=await get('/dataset-drafts/'+id);assert.equal(report.corrected.clips[0].text,corrected);assert.equal(report.corrected.clips[0].label_source,'manual');assert.ok(report.corrected.clips[0].reviewed);
  report.version=await click(page.getByRole('button',{name:'确认并发布数据版本',exact:true}),'/dataset-drafts/'+id+'/publish');save();
  assert.equal(report.version.clips[0].text,corrected);check('真实试听、修正文案通过并发布训练数据版本');
  await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();
  await page.locator('.model-management > details').filter({has:page.locator('summary').filter({hasText:'已发布数据版本'})}).locator('summary').first().click();
  const versionRow=page.locator('.model-management > details:not(.draft-management) > .version-row').filter({hasText:report.version.id.slice(0,8)});
  report.versionPreview=await click(versionRow.getByRole('button',{name:'查看删除范围',exact:true}),'/dataset-versions/'+report.version.id+'/delete-preview','GET');
  assert.ok(report.versionPreview.deletable);assert.ok(report.versionPreview.files.every(f=>path.resolve(f.path).startsWith(path.resolve(report.version.path)+path.sep)));
  report.deletedVersion=await click(page.getByRole('button',{name:'确认删除此数据版本',exact:true}),'/dataset-versions/'+report.version.id,'DELETE');
  assert.equal(report.deletedVersion.state,'DELETED');assert.ok(!fs.existsSync(report.version.path));
  const draftRow=page.locator('.draft-management > .version-row').filter({hasText:id.slice(0,8)});
  report.draftPreview=await click(draftRow.getByRole('button',{name:'查看删除范围',exact:true}),'/dataset-drafts/'+id+'/delete-preview','GET');
  assert.ok(report.draftPreview.deletable);assert.ok(report.draftPreview.files.every(f=>path.resolve(f.path).startsWith(path.join(root,'data/drafts',id)+path.sep)));
  report.deletedDraft=await click(page.getByRole('button',{name:'确认删除此标注草稿',exact:true}),'/dataset-drafts/'+id,'DELETE');
  assert.equal(report.deletedDraft.state,'DELETED');assert.ok(!fs.existsSync(path.join(root,'data/drafts',id)));
  assert.equal(sha(copied),report.sourceSha);assert.equal(sha(source),report.sourceSha);check('管理页真实删除本次测试版本与草稿，原始音频及纯语音输入副本不变');
  const model=page.locator('.model-card').filter({hasText:'21786e5fe9d652f591a44d3c368ae8fe'});
  while(!await model.count()) await page.getByRole('group',{name:'模型版本分页',exact:true}).getByRole('button',{name:'下一页',exact:true}).click();
  report.modelProtection=await click(model.getByRole('button',{name:'查看删除范围',exact:true}),'/models/21786e5fe9d652f591a44d3c368ae8fe/delete-preview','GET');
  assert.ok(!report.modelProtection.deletable);assert.ok(await page.getByRole('button',{name:'确认删除此模型版本',exact:true}).isDisabled());
  await page.getByRole('region',{name:'删除确认',exact:true}).getByRole('button',{name:'取消',exact:true}).click();check('管理页展示在用模型删除保护，未删除用户模型');
  report.savedAfter=(await get('/saved-speech?page_size=10')).items.map(x=>({id:x.id,sha256:x.audio.sha256}));assert.deepEqual(report.savedAfter,report.savedBefore);
  await page.screenshot({path:path.join(dir,'management.png')});assert.deepEqual(report.errors,[]);report.status='PASS';
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(dir,'failure.png')}).catch(()=>{});
}finally{if(context)await context.close();report.finished=new Date().toISOString();save();console.log(JSON.stringify({status:report.status,error:report.error,report:path.join(dir,'report.json')}));}})();
