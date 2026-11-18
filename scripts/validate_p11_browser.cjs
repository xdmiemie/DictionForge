const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'), runDir=path.join(root,'data/p11-browser',new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(runDir,{recursive:true});
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const evidence=JSON.parse(fs.readFileSync(path.join(root,'data/p11-http.json'),'utf8'));
const target=evidence.split_result.id, base='http://127.0.0.1:7867';
const report={started:new Date().toISOString(),runDir,target,checks:[],screenshots:[],consoleErrors:[],pageErrors:[],httpErrors:[]};
let context,page;
const pass=(name,detail)=>report.checks.push({name,status:'PASS',detail});
const get=async route=>{const r=await context.request.get(base+'/api'+route);assert.equal(r.status(),200);return r.json();};
const shot=async(name,locator)=>{const file=path.join(runDir,name+'.png');await locator.screenshot({path:file});report.screenshots.push({path:file,sha256:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')});};
async function waitJob(id){for(let i=0;i<1200;i++){const j=await get('/jobs/'+id);if(!['QUEUED','RUNNING'].includes(j.state)){assert.equal(j.state,'COMPLETED',JSON.stringify(j));return j;}await new Promise(r=>setTimeout(r,500));}throw Error('job timeout '+id);}
(async()=>{try{
  const temp=path.join(runDir,'temp');fs.mkdirSync(temp);
  context=await chromium.launchPersistentContext(path.join(runDir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});
  page=await context.newPage();page.on('pageerror',e=>report.pageErrors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.consoleErrors.push(m.text());});page.on('response',r=>{if(r.status()>=400)report.httpErrors.push({url:r.url(),status:r.status()});});
  const before=await get('/augmentations/'+target);assert.ok(['REVIEW_REQUIRED','PUBLISHED'].includes(before.state));let published=before;report.resumedFromPublished=before.state==='PUBLISHED';
  await page.goto(base);await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();
  const panel=page.getByRole('region',{name:'当前句回流'});
  if(before.state==='REVIEW_REQUIRED'){
  await panel.getByText(/等待逐段校对/).waitFor();
  const clips=panel.locator('.review-clip');assert.equal(await clips.count(),3);
  const expected=['今天我们一起去公园散步。','湖边的风景很美。'];
  for(let i=0;i<3;i++){
    const clip=clips.nth(i);const audio=await clip.locator('audio').evaluate(async a=>{a.muted=true;await a.play();await new Promise(r=>setTimeout(r,200));a.pause();return {duration:a.duration,currentTime:a.currentTime,readyState:a.readyState,subjectiveListening:'NOT_PERFORMED'};});
    assert.ok(audio.duration>1&&audio.currentTime>0);report.checks.push({name:'拆片实际解码 '+i,status:'PASS',detail:audio});
    if(i<2){assert.equal(before.draft.clips[i].text,expected[i]);await clip.getByRole('textbox').fill(expected[i]);await clip.getByLabel('已试听并确认标注',{exact:true}).check();}
    else await clip.getByLabel('排除此片段',{exact:true}).check();
  }
  const edited=page.waitForResponse(r=>r.url().includes('/api/dataset-drafts/'+target)&&r.request().method()==='PATCH');
  await panel.getByRole('button',{name:'保存回流校对',exact:true}).click();assert.equal((await edited).status(),200);
  const reviewed=await get('/augmentations/'+target);assert.ok(reviewed.draft.clips.slice(0,2).every(c=>c.reviewed));assert.equal(reviewed.draft.clips[2].excluded,true);
  pass('逐段保留准确ASR文本并排除截断残片',{review_method:'Known source text compared with each independent ASR transcript; muted playback verified decoding, not subjective hearing',excluded:reviewed.draft.clips[2].id});
  await shot('01-reviewed-fragments',panel);
  const accepted=page.waitForResponse(r=>r.url().endsWith('/augmentations/'+target+'/publish')&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'准备特征并发布回流版本',exact:true}).click();const job=await(await accepted).json();report.publishJob=job.id;
  await waitJob(job.id);published=await get('/augmentations/'+target);
  }
  report.published=published;assert.equal(published.state,'PUBLISHED');assert.equal(published.added_count,2);
  await panel.getByRole('button',{name:'用新版本微调',exact:true}).waitFor({timeout:15000});await panel.getByRole('button',{name:'用新版本微调',exact:true}).click();
  const training=page.locator('#training-config');await training.waitFor({state:'visible'});
  await page.waitForFunction(expected=>document.querySelector('#training-config select[aria-label="训练数据版本"]')?.value===expected,published.training_defaults.version_id);
  await page.waitForFunction(expected=>document.querySelector('#training-config select[aria-label="初始化权重"]')?.value===expected,published.training_defaults.initial_model);
  assert.equal(await training.getByLabel('训练数据版本',{exact:true}).inputValue(),published.training_defaults.version_id);
  assert.equal(await training.getByLabel('初始化权重',{exact:true}).inputValue(),published.training_defaults.initial_model);
  pass('发布后跳转并预选新版本和来源模型',published.training_defaults);await shot('02-source-initialization',training);
  await training.getByLabel('初始化权重',{exact:true}).selectOption('official');assert.equal(await training.getByLabel('初始化权重',{exact:true}).inputValue(),'official');
  pass('可切换官方底模，新训练入口独立于完整续训',{});
  const versions=await get('/datasets'),version=versions.find(v=>v.id===published.version_id);
  assert.deepEqual(version.clips.filter(c=>c.split==='validation').map(c=>[c.id,c.sha256]),evidence.parent.clips.filter(c=>c.split==='validation').map(c=>[c.id,c.sha256]));
  pass('真实留出保持不变，新增两片来自当前句',{version:version.id,clips:version.clips.length,added:version.clips.slice(-2).map(c=>({text:c.text,provenance:c.provenance}))});
  await page.setViewportSize({width:390,height:844});await shot('03-narrow-training',training);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  assert.equal(report.consoleErrors.length,0);assert.equal(report.pageErrors.length,0);assert.equal(report.httpErrors.length,0);report.status='PASS';
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await shot('failure',page.locator('body')).catch(()=>{});}
finally{if(context)await context.close();report.finished=new Date().toISOString();const json=JSON.stringify(report,null,2);fs.writeFileSync(path.join(runDir,'report.json'),json);fs.writeFileSync(path.join(root,'data/p11-browser/latest.json'),json);console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error,report:path.join(runDir,'report.json')}));}})();
