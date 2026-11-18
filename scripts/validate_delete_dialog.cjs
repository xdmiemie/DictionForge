// All DELETEs are intercepted; real API calls only preview existing records.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(path.resolve(path.dirname(process.execPath),'../node_modules/playwright'));
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:7867',dir=path.join(root,'data/delete-preview-bug',new Date().toISOString().replace(/[:.]/g,'-'));
fs.mkdirSync(dir,{recursive:true});
const report={started:new Date().toISOString(),checks:[],errors:[],requests:[],dir};let context,page;
const check=(name,details={})=>report.checks.push({name,status:'PASS',...details});
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('condition timeout');}
async function get(route){const r=await context.request.get(base+'/api'+route);assert.ok(r.ok(),await r.text());return r.json();}
const dialog=()=>page.getByRole('dialog',{name:'删除范围',exact:true});
async function inViewport(){const bounds=await dialog().boundingBox(),view=page.viewportSize();assert.ok(bounds&&bounds.y>=0&&bounds.x>=0&&bounds.y+bounds.height<=view.height+1&&bounds.x+bounds.width<=view.width+1);assert.ok(await dialog().evaluate(el=>el.matches(':modal')));return bounds;}
async function ready(){await dialog().getByRole('region',{name:'删除确认',exact:true}).waitFor();await until(async()=>await dialog().getAttribute('aria-busy')==='false');}
async function cancel(){await dialog().getByRole('button',{name:'取消',exact:true}).click();await until(async()=>await dialog().count()===0);}
(async()=>{try{
 const temp=path.join(dir,'temp');fs.mkdirSync(temp);
 context=await chromium.launchPersistentContext(path.join(dir,'profile'),{executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true,viewport:{width:1440,height:1050},locale:'zh-CN',env:{...process.env,TEMP:temp,TMP:temp,TMPDIR:temp}});
 page=await context.newPage();page.on('pageerror',e=>report.errors.push(String(e)));
 const before={models:(await get('/models')).map(x=>x.id),versions:(await get('/datasets')).map(x=>x.id),drafts:(await get('/dataset-drafts')).map(x=>x.id)};
 await page.route('**/api/**',async route=>{const req=route.request();if(req.method()==='DELETE'){report.requests.push({method:'DELETE',url:req.url(),body:req.postDataJSON(),intercepted:true});await route.fulfill({status:409,json:{detail:'测试：删除预览已过期，请重新查看范围。'}});}else await route.continue();});
 await page.goto(base);await page.getByRole('button',{name:'03数据与模型管理',exact:true}).click();
 const modelButton=page.locator('.model-card').getByRole('button',{name:'查看删除范围',exact:true}).first();
 await modelButton.click();await ready();check('模型删除范围在当前视野内弹出',{bounds:await inViewport()});
 assert.ok(await dialog().getByRole('button',{name:'取消',exact:true}).evaluate(el=>document.activeElement===el));
 await page.keyboard.press('Escape');await until(async()=>await dialog().count()===0);
 assert.ok(await modelButton.evaluate(el=>document.activeElement===el));check('键盘焦点进入对话框，Esc关闭后返回原按钮');
 await page.locator('.model-management > details:not(.draft-management)').filter({has:page.locator('summary').filter({hasText:'已发布数据版本'})}).evaluate(el=>el.open=true);
 const versionButton=page.locator('.model-management > details:not(.draft-management) > .version-row').first().getByRole('button',{name:'查看删除范围',exact:true});
 await versionButton.click();await ready();check('数据版本预览在当前视野内显示',{bounds:await inViewport()});await cancel();
 const draftButton=page.locator('.draft-management > .version-row').first().getByRole('button',{name:'查看删除范围',exact:true});
 await page.setViewportSize({width:390,height:844});await draftButton.click();await ready();check('草稿预览在390px窄屏完整容纳',{bounds:await inViewport()});
 await page.screenshot({path:path.join(dir,'mobile.png')});await cancel();await page.setViewportSize({width:1440,height:1050});
 const modelId=before.models.find(id=>id!=='official'),url='**/api/models/'+modelId+'/delete-preview';
 // Use the first rendered model's own identity for deterministic slow/error fixtures.
 const firstId=await modelButton.locator('..').locator('p.path-text').first().innerText().then(t=>t.match(/版本 (\S+)/)[1]);
 const previewUrl='**/api/models/'+firstId+'/delete-preview';let release;
 const gate=new Promise(resolve=>{release=resolve;});
 await page.route(previewUrl,async route=>{await gate;await route.fulfill({json:{preview_id:'late-token',files:[],references:[],deletable:true,total_bytes:0}});});
 await modelButton.click();await dialog().getByRole('status').waitFor();await inViewport();
 assert.equal(await dialog().getByRole('button',{name:/确认删除此/}).count(),0);await cancel();
 await draftButton.click();await ready();const title=await dialog().getByRole('heading').innerText();release();
 await page.waitForTimeout(200);assert.equal(await dialog().getByRole('heading').innerText(),title);assert.match(title,/标注草稿/);await cancel();
 await page.unroute(previewUrl);check('慢请求即时提示且可取消，迟到结果不覆盖新对象');
 let calls=0;
 await page.route(previewUrl,async route=>{calls++;if(calls===1)await route.fulfill({status:503,json:{detail:'测试：暂时无法读取删除范围'}});else await route.fulfill({json:{preview_id:'fixture-token',files:[],references:[],deletable:true,total_bytes:0}});});
 await modelButton.click();await dialog().getByRole('alert').waitFor();assert.match(await dialog().getByRole('alert').innerText(),/暂时无法读取/);await inViewport();
 await dialog().getByRole('button',{name:'重新查看删除范围',exact:true}).click();await ready();assert.equal(calls,2);check('请求失败在对话框提示并可重试');
 await dialog().getByRole('button',{name:'确认删除此模型版本',exact:true}).click();await dialog().getByRole('alert').waitFor();assert.match(await dialog().getByRole('alert').innerText(),/已过期/);
 assert.equal(report.requests.at(-1).body.preview_id,'fixture-token');await inViewport();await page.screenshot({path:path.join(dir,'error.png')});await cancel();check('确认失败仍在框内可见，冻结确认令牌未变（DELETE已拦截）');
 await page.unroute(previewUrl);
 const protectedId='21786e5fe9d652f591a44d3c368ae8fe';const protectedCard=page.locator('.model-card').filter({hasText:protectedId});
 for(let i=0;i<20&&!await protectedCard.count();i++)await page.getByRole('group',{name:'模型版本分页',exact:true}).getByRole('button',{name:'下一页'}).click();
 await protectedCard.getByRole('button',{name:'查看删除范围',exact:true}).click();await ready();assert.ok(await dialog().getByRole('button',{name:'确认删除此模型版本',exact:true}).isDisabled());
 await page.screenshot({path:path.join(dir,'protected.png')});await cancel();check('在用模型的引用保护仍阻止确认删除');
 const after={models:(await get('/models')).map(x=>x.id),versions:(await get('/datasets')).map(x=>x.id),drafts:(await get('/dataset-drafts')).map(x=>x.id)};
 assert.deepEqual(after,before);assert.deepEqual(report.errors,[]);report.status='PASS';
}catch(e){report.status='FAILED';report.error=e.stack??String(e);process.exitCode=1;if(page)await page.screenshot({path:path.join(dir,'failure.png')}).catch(()=>{});
}finally{if(context)await context.close();report.finished=new Date().toISOString();fs.writeFileSync(path.join(dir,'report.json'),JSON.stringify(report,null,2));fs.writeFileSync(path.join(root,'data/delete-preview-bug/latest.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({status:report.status,checks:report.checks.length,error:report.error,dir}));}})();
