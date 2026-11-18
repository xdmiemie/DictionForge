// Real DELETEs are restricted to a separate fixture database on port 7869.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'..'),fixture=JSON.parse(fs.readFileSync(path.join(root,'data/cascade-ui-fixture/latest.json'),'utf8')),base='http://127.0.0.1:7869';
const report={fixture,started:new Date().toISOString(),checks:[],errors:[]};let context,page;
const sha=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const check=name=>{report.checks.push({name,status:'PASS'});console.log(name);};
async function get(route){const r=await context.request.get(base+'/api'+route);assert.ok(r.ok(),await r.text());return r.json();}
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('timeout');}
async function request(button,route,method){const promise=page.waitForResponse(r=>new URL(r.url()).pathname==='/api'+route&&r.request().method()===method);await button.click();const r=await promise;assert.ok(r.ok(),await r.text());return r.json();}
const dialog=()=>page.getByRole('dialog',{name:'删除范围',exact:true});
(async()=>{try{
 context=await chromium.launchPersistentContext(path.join(fixture.area,'browser'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN'});
 assert.equal(path.resolve((await get('/health')).data_root),path.resolve(fixture.data_root));
 assert.equal((await get('/saved-speech')).total,1);
 page=await context.newPage();page.on('pageerror',e=>report.errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')report.errors.push(m.text());});
 await page.goto(base);await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();
 const parent=page.locator('.model-card').filter({has:page.getByRole('heading',{name:'级联验收 parent',exact:true})});
 report.modelPreview=await request(parent.getByRole('button',{name:'查看删除范围',exact:true}),'/models/parent/delete-preview','GET');
 assert.equal(report.modelPreview.mode,'cascade');assert.ok(report.modelPreview.deletable);
 for(const key of ['parent','child','feedback','feedback-draft',fixture.run_id])assert.ok(report.modelPreview.objects.some(x=>x.id===key));
 assert.ok(!report.modelPreview.objects.some(x=>x.id==='upstream'||x.id==='keep'));
 await until(async()=>await dialog().getAttribute('aria-busy')==='false');
 assert.equal(await dialog().locator('.cascade-objects article').count(),5);await dialog().getByRole('group',{name:'关联删除对象分页',exact:true}).getByRole('button',{name:'下一页'}).click();
 check('模型预览递归列出关联对象，每页5项，已启用和暂停恢复点可一并删除');
 await page.screenshot({path:path.join(fixture.area,'model-preview.png')});
 report.modelDelete=await request(dialog().getByRole('button',{name:'确认删除此模型版本及关联记录',exact:true}),'/models/parent','DELETE');
 assert.equal(report.modelDelete.state,'DELETED');await until(async()=>await dialog().count()===0);
 assert.deepEqual((await get('/models')).map(x=>x.id).sort(),['independent','official']);
 assert.ok((await get('/datasets')).some(x=>x.id==='upstream'));assert.equal((await get('/synthesis/selection')).active_model,null);
 assert.equal((await get('/jobs')).length,0);check('确认实际删除模型、派生模型、暂停运行、回流及草稿，并清除启用指向');
 await page.locator('.model-management > details:not(.draft-management)').filter({has:page.locator('summary').filter({hasText:'已发布数据版本'})}).evaluate(el=>el.open=true);
 const upstream=page.locator('.model-management > details:not(.draft-management) > .version-row').filter({hasText:'upstream'});
 report.datasetPreview=await request(upstream.getByRole('button',{name:'查看删除范围',exact:true}),'/dataset-versions/upstream/delete-preview','GET');
 assert.ok(report.datasetPreview.deletable&&report.datasetPreview.objects.some(x=>x.id==='independent')&&report.datasetPreview.objects.some(x=>x.id==='revision'));
 await until(async()=>await dialog().getAttribute('aria-busy')==='false');
 report.datasetDelete=await request(dialog().getByRole('button',{name:'确认删除此数据版本及关联记录',exact:true}),'/dataset-versions/upstream','DELETE');
 assert.equal(report.datasetDelete.state,'DELETED');assert.equal((await get('/datasets')).length,0);assert.equal((await get('/dataset-drafts')).length,0);
 assert.deepEqual((await get('/models')).map(x=>x.id),['official']);check('数据版本实际连同派生版本、关联模型与借用音频草稿删除');
 assert.equal(sha(fixture.source),fixture.source_sha256);assert.equal(sha(fixture.asset),fixture.asset_sha256);assert.equal(sha(fixture.saved),fixture.saved_sha256);
 assert.equal((await get('/saved-speech')).total,1);check('原始输入、共享资产和独立作品的文件与哈希保留');
 await page.reload();await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();await until(async()=>await page.locator('.model-card').count()===1);
 assert.deepEqual(report.errors,[]);report.status='PASS';
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(fixture.area,'failure.png')}).catch(()=>{});
}finally{if(context)await context.close();report.finished=new Date().toISOString();fs.writeFileSync(path.join(fixture.area,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(root,'data/cascade-ui-fixture/result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error,area:fixture.area}));}})();
