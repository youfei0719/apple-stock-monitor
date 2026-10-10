import ts from 'typescript';
import {readFileSync} from 'node:fs';import assert from 'node:assert/strict';
// 单独加载 API 模块时，用测试值代替 Vite 环境变量；网络请求均由下方 mock 拦截。
const s=readFileSync(new URL('../src/lib/api.ts', import.meta.url),'utf8').replace('import.meta.env.VITE_API_BASE','undefined');
const js=ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText;
const {summarizeTask, api}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const pn='P1',at=new Date().toISOString(),task={part_number:pn,stores:[{number:'S1'},{number:'S2'}],paused:false,expires_at:null,latest:{stores:{S1:{P1:{state:'unavailable',updated_at:at}}},available_count:88}};
const checks=[];const check=(name,fn)=>{fn();checks.push(name);console.log('PASS '+name);};
check('missing-store-is-unknown',()=>{const r=summarizeTask(task);assert.equal(r.state,'unknown');assert.equal(r.partialUnknown,true);});
check('available-does-not-hide-missing-store',()=>{task.latest.stores.S1.P1.state='available';const r=summarizeTask(task);assert.equal(r.state,'available');assert.equal(r.partialUnknown,true);assert.equal(r.availableCount,1);});
check('old-store-and-other-sku-excluded',()=>{task.latest.stores.OLD={P1:{state:'available'}};task.latest.stores.S2={OLD:{state:'available'}};assert.equal(summarizeTask(task).availableCount,1);});
check('all-unavailable-required',()=>{task.latest.stores.S1.P1.state='unavailable';task.latest.stores.S2.P1={state:'unavailable',updated_at:at};assert.equal(summarizeTask(task).state,'unavailable');assert.equal(summarizeTask(task).partialUnknown,false);});
check('paused-priority-and-no-unknown-warning',()=>{task.paused=true;task.expires_at='2000-01-01T00:00:00Z';const r=summarizeTask(task);assert.equal(r.state,'paused');assert.equal(r.partialUnknown,false);});
console.log(checks.length+' meaningful stock aggregation checks passed');

const previousFetch=globalThis.fetch;
try {
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:[{loc:['body','email'],msg:'value is not a valid email address',type:'value_error'}]}),{status:422});
  await assert.rejects(()=>api.createTask({}),error=>error.message==='请填写有效的邮箱地址');console.log('PASS validation-error-is-user-language');
  globalThis.fetch=async()=>{throw new TypeError('Failed to fetch')};
  await assert.rejects(()=>api.createTask({}),error=>error.message==='连接失败，请检查网络后重试');console.log('PASS network-error-is-user-language');
}finally{globalThis.fetch=previousFetch;}
