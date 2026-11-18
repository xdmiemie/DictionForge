// Real DOM workflow. HTTP reads only inspect outcomes; all business writes use UI.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:7867',ledger=path.join(root,'data/p12-browser.json'),phase=process.argv[2];
const report=fs.existsSync(ledger)?JSON.parse(fs.readFileSync(ledger,'utf8')):{started:new Date().toISOString(),phases:{}};
const runDir=path.join(root,'data/p12-browser',phase+'-'+new Date().toISOString().replace(/[:.]/g,'-'));fs.mkdirSync(runDir,{recursive:true});
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
let context,page;const save=()=>fs.writeFileSync(ledger,JSON.stringify(report,null,2));
async function get(route){const r=await context.request.get(base+'/api'+route);assert.ok(r.ok(),await r.text());return r.json();}
async function until(fn,ms=900000){const deadline=Date.now()+ms;while(Date.now()<deadline){const r=await fn();if(r)return r;await new Promise(r=>setTimeout(r,300));}throw Error('timeout');}
async function finished(id){return until(async()=>{const j=await get('/jobs/'+id);if(['QUEUED','RUNNING'].includes(j.state))return;assert.equal(j.state,'COMPLETED',JSON.stringify(j));return j;});}
async function clickRequest(button,suffix,method='POST'){const pending=page.waitForResponse(r=>r.url().endsWith('/api'+suffix)&&r.request().method()===method,{timeout:60000});await button.click();const r=await pending;assert.ok(r.ok(),await r.text());return r.json();}
async function play(locator){const result=await locator.evaluate(async a=>{a.muted=true;await a.play();await new Promise(r=>setTimeout(r,250));a.pause();return {duration:a.duration,time:a.currentTime,subjective:'User acceptance is recorded separately'};});assert.ok(result.duration>0&&result.time>0);return result;}
async function selectRun(){await page.locator('.job-row').filter({hasText:report.train.id.slice(0,8)}).click();await page.locator('.task-detail').waitFor();return page.locator('.task-detail');}
async function configure(target){const cfg=page.locator('#training-config');await cfg.waitFor();const stages=cfg.locator('.stage-config');for(let i=0;i<2;i++){await stages.nth(i).getByLabel('停止条件').selectOption('steps');await stages.nth(i).getByLabel('目标累计优化器步数').fill(String(i?2:target));}
 await stages.nth(1).locator('summary').click();await stages.nth(1).getByLabel('梯度累积').fill('1');return cfg;}
async function prepare(){
 const panel=page.locator('.dataset-panel');
 if(!report.scan){await panel.getByPlaceholder('例如 E:\\角色录音\\中文素材').fill('E:\\DictionForge\\data\\fixtures\\景元 训练');await panel.getByPlaceholder('为这组声音起一个名字').fill('景元 · P12全流程');report.scan=await clickRequest(panel.getByRole('button',{name:'扫描录音',exact:true}),'/datasets/scan');save();}
 const scan=await finished(report.scan.id);report.scan=scan;save();
 await panel.getByLabel('扫描记录').selectOption(scan.id);await panel.locator('.scan-file').first().waitFor();assert.equal(await panel.locator('.scan-file').count(),6);
 if(!report.prepare){report.scanPlayback=await play(panel.locator('.scan-file audio').first());await panel.locator('.scan-file').last().getByLabel('排除',{exact:true}).check();
 await panel.getByText('语音切分设置',{exact:true}).click();await panel.getByLabel('最短停顿（秒）').fill('3');
 report.prepare=await clickRequest(panel.getByRole('button',{name:'整理数据集 · 切分并转写',exact:true}),'/datasets/prepare');save();}
 report.prepare=await finished(report.prepare.id);save();const draftId=report.prepare.result.draft_id;
 const review=page.locator('section.panel').filter({has:page.getByRole('heading',{name:'标注草稿',exact:true})});await review.getByLabel('选择草稿').selectOption(draftId);await review.locator('.review-clip').first().waitFor();
 let draft=await get('/dataset-drafts/'+draftId);assert.equal(draft.clips.length,5);
 if(!report.asr){report.asr=await clickRequest(review.locator('.review-clip').first().getByRole('button',{name:'重新识别',exact:true}),'/dataset-drafts/'+draftId+'/asr');save();}
 report.asr=await finished(report.asr.id);save();
 if(!report.version){await review.getByRole('button',{name:'重新读取草稿',exact:true}).click();draft=await get('/dataset-drafts/'+draftId);report.draftBeforeReview=draft;
 const clips=review.locator('.review-clip');await until(async()=>await clips.count()===5);report.draftPlayback=[];
 for(let i=0;i<draft.clips.length;i++){const clip=clips.nth(i);report.draftPlayback.push(await play(clip.locator('audio')));const known=fs.readFileSync(draft.clips[i].source_path.replace(/\.wav$/i,'.lab'),'utf8').replace(/^\uFEFF/,'').trim();assert.ok(known);await clip.getByRole('textbox').fill(known);await clip.getByLabel('文本已核对',{exact:true}).check();}
 // Existing .lab labels are authoritative; re-ASR text is archived for comparison.
 await clickRequest(review.getByRole('button',{name:'保存标注修改',exact:true}),'/dataset-drafts/'+draftId,'PATCH');
 report.version=await clickRequest(review.getByRole('button',{name:'确认并发布数据版本',exact:true}),'/dataset-drafts/'+draftId+'/publish');save();}
 const cfg=page.locator('#training-config');await cfg.getByLabel('训练数据版本',{exact:true}).selectOption(report.version.id);
 if(!report.features){report.features=await clickRequest(cfg.getByRole('button',{name:/^(准备训练特征|校验并补齐特征)$/}),'/dataset-versions/'+report.version.id+'/features');save();}
 report.features=await finished(report.features.id);save();await cfg.screenshot({path:path.join(runDir,'features.png')});
}
async function correct(){
 report.priorVersion=report.priorVersion??report.version;save();
 if(!report.correctDraft){const row=page.locator('.version-row').filter({hasText:report.priorVersion.id.slice(0,8)});report.correctDraft=await clickRequest(row.getByRole('button',{name:'修改标注',exact:true}),'/dataset-versions/'+report.priorVersion.id+'/draft');save();}
 const review=page.locator('section.panel').filter({has:page.getByRole('heading',{name:'标注草稿',exact:true})});await review.getByLabel('选择草稿').selectOption(report.correctDraft.id);const clips=review.locator('.review-clip');await clips.first().waitFor();
 const draft=await get('/dataset-drafts/'+report.correctDraft.id);report.labelCorrections=[];
 for(let i=0;i<draft.clips.length;i++){const c=draft.clips[i],known=fs.readFileSync(c.source_path.replace(/\.wav$/i,'.lab'),'utf8').replace(/^\uFEFF/,'').trim();report.labelCorrections.push({id:c.id,before:c.text,after:known});await clips.nth(i).getByRole('textbox').fill(known);await clips.nth(i).getByLabel('文本已核对',{exact:true}).check();}
 await clickRequest(review.getByRole('button',{name:'保存标注修改',exact:true}),'/dataset-drafts/'+draft.id,'PATCH');report.version=await clickRequest(review.getByRole('button',{name:'确认并发布数据版本',exact:true}),'/dataset-drafts/'+draft.id+'/publish');save();
 const cfg=page.locator('#training-config');await cfg.getByLabel('训练数据版本',{exact:true}).selectOption(report.version.id);report.features=await clickRequest(cfg.getByRole('button',{name:'准备训练特征',exact:true}),'/dataset-versions/'+report.version.id+'/features');save();report.features=await finished(report.features.id);save();await review.screenshot({path:path.join(runDir,'corrected-labels.png')});
}
async function trainPause(){
 if(!report.train){const cfg=await configure(40);await cfg.getByLabel('训练数据版本',{exact:true}).selectOption(report.version.id);await cfg.getByLabel('初始化权重',{exact:true}).selectOption('official');
 if(!report.preflight){report.preflight=await clickRequest(cfg.getByRole('button',{name:'检查训练配置与显存',exact:true}),'/training/preflight');save();}else{report.preflight=await clickRequest(cfg.getByRole('button',{name:'检查训练配置与显存',exact:true}),'/training/preflight');save();}
 report.preflight=await finished(report.preflight.id);assert.ok(report.preflight.result.ready);save();
 report.train=await clickRequest(cfg.getByRole('button',{name:'开始训练',exact:true}),'/training');save();}
 await until(async()=>{const j=await get('/training/'+report.train.id);if(j.state==='PAUSED')return true;assert.ok(!['FAILED','COMPLETED'].includes(j.state),JSON.stringify(j));return j.stage_state?.stages?.s2?.optimizer_updates>=1;});
 const detail=await selectRun();
 if((await get('/training/'+report.train.id)).state!=='PAUSED')await clickRequest(detail.getByRole('button',{name:'暂停并保存恢复点',exact:true}),'/training/'+report.train.id+'/controls');
 report.paused=await until(async()=>{const r=await get('/training/'+report.train.id);return r.state==='PAUSED'?r:null;});report.points=await get('/training/'+report.train.id+'/checkpoints');assert.ok(report.points.last_valid);save();await detail.screenshot({path:path.join(runDir,'paused.png')});
}
async function resume(){const detail=await selectRun();const prior=await get('/training/'+report.train.id);assert.equal(prior.state,'PAUSED');report.beforeResume=prior;
 await clickRequest(detail.getByRole('button',{name:'继续训练',exact:true}),'/training/'+report.train.id+'/controls');report.completed=await finished(report.train.id);report.finalTraining=await get('/training/'+report.train.id);save();
 const exported=await until(async()=>{const r=await get('/training/'+report.train.id);const e=r.exports.find(x=>x.state==='COMPLETED');return e??null;});report.export=exported;report.modelId=exported.result.model_id??exported.result.model?.id;assert.ok(report.modelId,JSON.stringify(exported));save();await detail.screenshot({path:path.join(runDir,'completed.png')});}
async function finish(){
 await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();await page.getByLabel('选择模型版本',{exact:true}).selectOption(report.modelId);
 const synth=page.locator('.synthesis-panel');await synth.locator('.reference-editor summary').click();await synth.getByLabel('已管理参考',{exact:true}).selectOption('563ea3259eed48938003d5bb0566d68e');await clickRequest(synth.getByRole('button',{name:'设为默认参考',exact:true}),'/models/'+report.modelId+'/reference');
 const enable=synth.getByRole('button',{name:'启用此模型',exact:true});if(await enable.count())await enable.click();
 const text=page.getByRole('region',{name:'中文文本合成'});await text.getByLabel('待朗读文本',{exact:true}).fill('你好，今天的练习已经完成，我们明天再见。');
 if(!report.tts){report.tts=await clickRequest(text.getByRole('button',{name:'生成语音',exact:true}),'/tts');save();}
 report.tts=await finished(report.tts.id);report.speech=await get('/tts/'+report.tts.id);save();
 const result=page.locator('.speech-playback[data-result-id="'+report.tts.id+'"]');await result.waitFor();report.playback=await play(result.getByLabel('完整合成音频',{exact:true}));
 if(!report.saved){await result.getByLabel('作品标题',{exact:true}).fill('P12完整流程·景元');report.saved=await clickRequest(result.getByRole('button',{name:'保存到作品库',exact:true}),'/tts/'+report.tts.id+'/save');save();}
 report.saved=await finished(report.saved.id);save();
 const append=page.getByRole('region',{name:'当前句回流'});
 if(!report.append){report.append=await clickRequest(append.getByRole('button',{name:'将当前句加入数据集',exact:true}),'/tts/'+report.tts.id+'/append');save();}
 report.append=await finished(report.append.id);report.augmentation=await get('/augmentations/'+report.append.id);assert.equal(report.augmentation.state,'PUBLISHED');save();
 await append.getByRole('button',{name:'用新版本微调',exact:true}).click();const cfg=await configure(2);
 await page.waitForFunction(id=>document.querySelector('select[aria-label="训练数据版本"]')?.value===id,report.augmentation.version_id);assert.equal(await cfg.getByLabel('初始化权重',{exact:true}).inputValue(),report.modelId);
 if(!report.secondTrain){report.secondPreflight=await clickRequest(cfg.getByRole('button',{name:'检查训练配置与显存',exact:true}),'/training/preflight');save();report.secondPreflight=await finished(report.secondPreflight.id);assert.ok(report.secondPreflight.result.ready);save();
 report.secondTrain=await clickRequest(cfg.getByRole('button',{name:'开始训练',exact:true}),'/training');save();}report.secondTrain=await finished(report.secondTrain.id);report.secondView=await get('/training/'+report.secondTrain.id);save();
 const exported=await until(async()=>{const r=await get('/training/'+report.secondTrain.id);return r.exports.find(x=>x.state==='COMPLETED');});report.secondExport=exported;save();await cfg.screenshot({path:path.join(runDir,'feedback-training.png')});
}
async function finalProbe(){
 const modelId=report.secondExport.result.model_version_id;
 await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();await page.getByLabel('选择模型版本',{exact:true}).selectOption(modelId);
 const synth=page.locator('.synthesis-panel');await synth.locator('.reference-editor summary').click();await synth.getByLabel('已管理参考',{exact:true}).selectOption('563ea3259eed48938003d5bb0566d68e');await clickRequest(synth.getByRole('button',{name:'设为默认参考',exact:true}),'/models/'+modelId+'/reference');
 const enable=synth.getByRole('button',{name:'启用此模型',exact:true});if(await enable.count())await enable.click();
 if(!report.secondProbe){report.secondProbe=await clickRequest(synth.getByRole('button',{name:'试听当前模型',exact:true}),'/models/'+modelId+'/probe');save();}
 report.secondProbe=await finished(report.secondProbe.id);assert.equal(report.secondProbe.result.audio.sample_rate,48000);save();
 report.secondProbePlayback=await play(synth.locator('audio[src="/api/audio/'+report.secondProbe.result.audio.audio_id+'"]'));save();await synth.locator('.fixed-probe').screenshot({path:path.join(runDir,'final-pair-probe.png')});
}
async function restore(){
 const modelId='21786e5fe9d652f591a44d3c368ae8fe';
 await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();await page.getByLabel('选择模型版本',{exact:true}).selectOption(modelId);
 const synth=page.locator('.synthesis-panel');await synth.locator('.reference-editor summary').click();await synth.getByLabel('已管理参考',{exact:true}).selectOption('563ea3259eed48938003d5bb0566d68e');await clickRequest(synth.getByRole('button',{name:'设为默认参考',exact:true}),'/models/'+modelId+'/reference');
 const enable=synth.getByRole('button',{name:'启用此模型',exact:true});if(await enable.count())await clickRequest(enable,'/models/'+modelId+'/enable');
 report.deliveredModel=modelId;save();await synth.screenshot({path:path.join(runDir,'delivered-model.png')});
}
(async()=>{const entry={started:new Date().toISOString(),runDir,errors:[]};report.phases[phase]=entry;try{
 const temp=path.join(runDir,'temp');fs.mkdirSync(temp);context=await chromium.launchPersistentContext(path.join(runDir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});page=await context.newPage();page.on('pageerror',e=>entry.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')entry.errors.push(m.text());});page.on('dialog',d=>d.accept());await page.goto(base);
 await ({prepare,correct,train_pause:trainPause,resume,finish,probe:finalProbe,restore}[phase])();assert.deepEqual(entry.errors,[]);entry.status='PASS';
}catch(e){entry.status='FAILED';entry.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(runDir,'failure.png')}).catch(()=>{});}
finally{if(context)await context.close();entry.finished=new Date().toISOString();save();fs.writeFileSync(path.join(runDir,'report.json'),JSON.stringify(entry,null,2));console.log(JSON.stringify({phase,status:entry.status,error:entry.error,report:ledger}));}})();
