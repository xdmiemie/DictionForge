// Actual single-result browser append, with a persistent ledger for safe retries.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:7867',ledger=path.join(root,'data/p11-browser-whole.json');
const report=fs.existsSync(ledger)?JSON.parse(fs.readFileSync(ledger,'utf8')):{started:new Date().toISOString(),checks:[]};
const runDir=path.join(root,'data/p11-browser-whole',new Date().toISOString().replace(/[:.]/g,'-'));fs.mkdirSync(runDir,{recursive:true});
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
let context,page;
const save=()=>fs.writeFileSync(ledger,JSON.stringify(report,null,2));
async function api(route,body){const r=await context.request[body?'post':'get'](base+'/api'+route,body?{data:body}:{});assert.ok(r.ok(),await r.text());return r.json();}
async function finished(id){for(let n=0;n<1200;n++){const j=await api('/jobs/'+id);if(!['QUEUED','RUNNING'].includes(j.state)){assert.equal(j.state,'COMPLETED',JSON.stringify(j));return j;}await new Promise(r=>setTimeout(r,500));}throw Error('timeout '+id);}
const pass=(name,detail)=>report.checks.push({name,status:'PASS',detail});
(async()=>{try{
  const temp=path.join(runDir,'temp');fs.mkdirSync(temp);
  context=await chromium.launchPersistentContext(path.join(runDir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});
  page=await context.newPage();report.errors=[];page.on('pageerror',e=>report.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.errors.push(m.text());});
  if(!report.tts){report.tts=await api('/tts',{request_id:crypto.randomUUID(),model_version_id:'21786e5fe9d652f591a44d3c368ae8fe',reference_id:'563ea3259eed48938003d5bb0566d68e',text:'今天阳光很好，我们一起出发吧。',language:'zh',split_rule:'none',parameters:{seed:2027,sample_steps:16}});save();}
  await finished(report.tts.id);report.speech=await api('/tts/'+report.tts.id);assert.equal(report.speech.result.sentences.length,1);assert.equal(report.speech.result.audio.sample_rate,48000);save();
  await page.goto(base);await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();
  const panel=page.getByRole('region',{name:'当前句回流'});
  if(!report.append){const button=panel.getByRole('button',{name:'将当前句加入数据集',exact:true});await button.waitFor();
    const accepted=page.waitForResponse(r=>r.url().endsWith('/tts/'+report.tts.id+'/append')&&r.request().method()==='POST');await button.click();report.append=await(await accepted).json();save();}
  await finished(report.append.id);const result=await api('/augmentations/'+report.append.id);report.result=result;save();
  assert.equal(result.state,'PUBLISHED');assert.equal(result.asr_calls,0);assert.equal(result.added_count,1);assert.equal(result.source_model_id,'21786e5fe9d652f591a44d3c368ae8fe');
  const versions=await api('/datasets'),version=versions.find(v=>v.id===result.version_id);assert.equal(version.clips.at(-1).text,report.speech.request.text);
  pass('单句当前结果从页面追加，复用原文本且零ASR',{tts:report.tts.id,append:result.id,version:result.version_id,source_model:result.source_model_id,text:version.clips.at(-1).text});
  const jump=panel.getByRole('button',{name:'用新版本微调',exact:true});await jump.waitFor({timeout:15000});await jump.click();
  const training=page.locator('#training-config');
  const matches=()=>page.waitForFunction(expected=>document.querySelector('#training-config select[aria-label="训练数据版本"]')?.value===expected.version_id&&document.querySelector('#training-config select[aria-label="初始化权重"]')?.value===expected.initial_model,result.training_defaults);
  await matches();await training.getByLabel('初始化权重',{exact:true}).selectOption('official');
  await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();await jump.click();await matches();
  pass('更换来源模型后正确预选，重复跳转恢复来源初值',result.training_defaults);
  await training.screenshot({path:path.join(runDir,'training.png')});
  await page.setViewportSize({width:390,height:844});await page.getByRole('navigation',{name:'主工作区'}).getByRole('button',{name:'02语音合成',exact:true}).click();await panel.screenshot({path:path.join(runDir,'append-narrow.png')});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);
  assert.deepEqual(report.errors,[]);pass('390px回流界面与浏览器无异常',{});report.status='PASS';delete report.error;
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;}
finally{if(context)await context.close();report.finished=new Date().toISOString();report.runDir=runDir;save();fs.writeFileSync(path.join(runDir,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,error:report.error,checks:report.checks.length,report:ledger}));}})();
