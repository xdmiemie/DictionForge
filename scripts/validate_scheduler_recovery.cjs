// Reuse a completed export's immutable snapshot: no new model version is made.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const original=process.argv[2];assert.match(original??'',/^[a-f0-9]{32}$/,'Pass the completed export job ID');
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:7867';
const area=path.join(root,'data/scheduler-browser',new Date().toISOString().replace(/[:.]/g,'-'));fs.mkdirSync(area,{recursive:true});
const report={started:new Date().toISOString(),original,checks:[],errors:[]};let context,page;
const check=name=>{report.checks.push({name,status:'PASS'});console.log(name);};
async function api(route,body){const response=body?await context.request.post(base+'/api'+route,{data:body}):await context.request.get(base+'/api'+route);assert.ok(response.ok(),await response.text());return response.json();}
async function until(fn){for(let i=0;i<150;i++){if(await fn())return;await new Promise(r=>setTimeout(r,200));}throw Error('timeout');}
(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(area,'browser'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN'});
 const health=await api('/health');assert.equal(path.resolve(health.workspace),root);assert.equal(path.resolve(health.data_root),path.join(root,'data'));assert.equal(health.scheduler.state,'ready');
 const previous=await api('/jobs/'+original);assert.equal(previous.kind,'model_export');assert.equal(previous.state,'COMPLETED');
 const before=(await api('/models')).find(x=>x.id===previous.payload.model_version_id);assert.ok(before&&before.status==='READY');
 const exported=await api('/model-exports/'+original+'/retry',{request_id:crypto.randomUUID()});
 const diagnostic=await api('/jobs/diagnostic',{request_id:crypto.randomUUID()});
 report.export_job_id=exported.id;report.diagnostic_job_id=diagnostic.id;report.timeline=[];
 await until(async()=>{const values=await Promise.all([api('/jobs/'+exported.id),api('/jobs/'+diagnostic.id)]);
  report.timeline.push({at:new Date().toISOString(),states:values.map(j=>({id:j.id,state:j.state,pid:j.pid,error:j.error}))});
  for(const j of values)assert.ok(!['FAILED','INTERRUPTED','CANCELLED'].includes(j.state),JSON.stringify(j.error));
  return values.every(j=>j.state==='COMPLETED');});
 const after=(await api('/models')).find(x=>x.id===before.id);assert.deepEqual(after.weights,before.weights);
 report.model={id:after.id,role:after.role,weights:after.weights,validation:after.validation};
 check('CPU导出与GPU诊断自动领取并完成，同一冻结导出保持模型ID及权重哈希');
 page=await context.newPage();page.on('pageerror',e=>report.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.errors.push(m.text());});
 await page.goto(base);await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();
 await page.getByRole('heading',{name:after.role,exact:true}).waitFor();
 assert.equal(await page.getByRole('alert',{name:'后台调度状态',exact:true}).count(),0);
 await page.screenshot({path:path.join(area,'exported-model.png')});check('真实导出模型在管理页可见，健康调度不显示异常提示');
 let status='retrying';
 await page.route('**/api/health',async route=>{const response=await route.fetch();const body=await response.json();body.scheduler={...body.scheduler,state:status,alive:status!=='stopped'};await route.fulfill({response,json:body});});
 await page.reload();await page.getByRole('alert',{name:'后台调度状态',exact:true}).filter({hasText:'正在自动重试'}).waitFor();
 await page.screenshot({path:path.join(area,'retrying.png')});check('模拟暂时异常时显示自动重试及任务保留提示');
 status='stopped';await page.setViewportSize({width:390,height:844});await page.reload();
 await page.getByRole('alert',{name:'后台调度状态',exact:true}).filter({hasText:'未响应'}).waitFor();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));check('模拟调度停止时明确提示，390px页面不横向溢出');
 await page.unroute('**/api/health');await page.reload();await until(async()=>await page.getByRole('alert',{name:'后台调度状态',exact:true}).count()===0);
 report.health=await api('/health');assert.ok(report.health.scheduler.alive&&report.health.scheduler.state==='ready');assert.deepEqual(report.errors,[]);
 check('恢复真实健康状态后异常提示消失，浏览器无错误');report.status='PASS';
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(area,'failure.png')}).catch(()=>{});
}finally{if(context)await context.close();report.finished=new Date().toISOString();report.area=area;fs.writeFileSync(path.join(area,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(root,'data/scheduler-browser/latest.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error,area}));}})();
