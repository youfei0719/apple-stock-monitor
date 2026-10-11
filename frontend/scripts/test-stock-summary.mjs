import ts from 'typescript';
import {readFileSync} from 'node:fs';import assert from 'node:assert/strict';
// 单独加载 API 模块时，用测试值代替 Vite 环境变量；网络请求均由下方 mock 拦截。
const s=readFileSync(new URL('../src/lib/api.ts', import.meta.url),'utf8').replace('import.meta.env.VITE_API_BASE','undefined');
const js=ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2020}}).outputText;
const {summarizeTask, isRenewalDue, remainingTimeLabel, stockUnknownReason, isWaitingForLaunch, membershipUpdateConfirmed, api}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
const pn='P1',at=new Date().toISOString(),task={part_number:pn,stores:[{number:'S1'},{number:'S2'}],paused:false,expires_at:null,latest:{stores:{S1:{P1:{state:'unavailable',updated_at:at}}},available_count:88}};
const checks=[];const check=(name,fn)=>{fn();checks.push(name);console.log('PASS '+name);};
check('missing-store-is-unknown',()=>{const r=summarizeTask(task);assert.equal(r.state,'unknown');assert.equal(r.partialUnknown,true);});
check('available-does-not-hide-missing-store',()=>{task.latest.stores.S1.P1.state='available';const r=summarizeTask(task);assert.equal(r.state,'available');assert.equal(r.partialUnknown,true);assert.equal(r.availableCount,1);});
check('old-store-and-other-sku-excluded',()=>{task.latest.stores.OLD={P1:{state:'available'}};task.latest.stores.S2={OLD:{state:'available'}};assert.equal(summarizeTask(task).availableCount,1);});
check('all-unavailable-required',()=>{task.latest.stores.S1.P1.state='unavailable';task.latest.stores.S2.P1={state:'unavailable',updated_at:at};assert.equal(summarizeTask(task).state,'unavailable');assert.equal(summarizeTask(task).partialUnknown,false);});
check('paused-priority-and-no-unknown-warning',()=>{task.paused=true;task.expires_at='2000-01-01T00:00:00Z';const r=summarizeTask(task);assert.equal(r.state,'paused');assert.equal(r.partialUnknown,false);});
console.log(checks.length+' meaningful stock aggregation checks passed');

const now=Date.parse('2026-10-11T10:00:00Z'),hour=3600000,day=24*hour;
const trial={created_at:new Date(now).toISOString(),expires_at:new Date(now+day).toISOString()};
check('new-trial-does-not-prompt-renewal',()=>assert.equal(isRenewalDue(trial,now),false));
check('trial-renewal-only-final-three-hours',()=>{assert.equal(isRenewalDue(trial,now+21*hour),true);assert.equal(isRenewalDue(trial,now+20*hour),false);});
check('account-renewal-only-final-three-days',()=>{const t={...trial,expires_at:new Date(now+30*day).toISOString()};assert.equal(isRenewalDue(t,now+26*day),false);assert.equal(isRenewalDue(t,now+27*day),true);});
check('short-lived-task-never-rounded-to-two-days',()=>{assert.equal(remainingTimeLabel(trial,now),'24 小时');assert.equal(remainingTimeLabel(trial,now-1000),'24 小时');assert.equal(remainingTimeLabel(trial,now+23.5*hour),'不足 1 小时');assert.equal(remainingTimeLabel(trial,now+day),'已到期');});
check('ineligible-reason-preserved-without-false-stock',()=>{const t={...task,paused:false,expires_at:null,latest:{stores:{S1:{P1:{state:'unknown',pickup_display:'ineligible'}},S2:{P1:{state:'unknown',pickup_display:'ineligible'}}}}};const r=summarizeTask(t);assert.equal(r.state,'unknown');assert.equal(r.unknownReason,'pickup_not_offered');assert.equal(r.availableCount,0);});
check('uniform-unknown-reason-not-overwritten-by-missing',()=>{const t={...task,paused:false,expires_at:null,latest:{stores:{S1:{P1:{state:'unknown',pickup_display:'new-apple-value'}},S2:{P1:{state:'unknown',pickup_display:'new-apple-value'}}}}};assert.equal(summarizeTask(t).unknownReason,'unrecognized_result');assert.equal(stockUnknownReason(),'missing_result');t.latest.stores.S2.P1.pickup_display='ineligible';assert.equal(summarizeTask(t).unknownReason,'mixed_result');});

const previousFetch=globalThis.fetch;
try {
  globalThis.fetch=async()=>new Response(JSON.stringify({detail:[{loc:['body','email'],msg:'value is not a valid email address',type:'value_error'}]}),{status:422});
  await assert.rejects(()=>api.createTask({}),error=>error.message==='请填写有效的邮箱地址');console.log('PASS validation-error-is-user-language');
  globalThis.fetch=async()=>{throw new TypeError('Failed to fetch')};
  await assert.rejects(()=>api.createTask({}),error=>error.message==='连接失败，请检查网络后重试');console.log('PASS network-error-is-user-language');
}finally{globalThis.fetch=previousFetch;}

const launch = {preorder_at:'2026-10-16T20:00:00+08:00'};
check('scheduled-does-not-expose-stale-stock',()=>{
  const t={...task,paused:false,expires_at:null,launch:{preorder_at:'2099-10-16T20:00:00+08:00'},latest:{stores:{S1:{P1:{state:'available',updated_at:at}}}}};
  const r=summarizeTask(t);assert.equal(r.state,'scheduled');assert.equal(r.availableCount,0);assert.equal(r.partialUnknown,false);assert.equal(r.unknownReason,undefined);assert.equal(r.updatedAt,undefined);
  assert.equal(summarizeTask({...t,paused:true}).state,'paused');
  assert.equal(summarizeTask({...t,expires_at:'2000-01-01T00:00:00Z'}).state,'expired');
});
check('preorder-boundary-honors-beijing-time',()=>{
  assert.equal(isWaitingForLaunch({launch},Date.parse('2026-10-16T11:59:59Z')),true);
  assert.equal(isWaitingForLaunch({launch,waiting_for_launch:true},Date.parse('2026-10-16T12:00:00Z')),false);
});

check('payment-failures-and-downgrade-do-not-claim-paid',()=>{
  for (const status of ['amount_mismatch','unknown_plan','refunded','pending']) {
    assert.equal(membershipUpdateConfirmed('free',[{order_id:'new',status}],'pro',new Set()),false);
  }
  assert.equal(membershipUpdateConfirmed('free',[],'pro',new Set()),false);
});
check('confirmed-payment-supports-renewal-and-upgrade',()=>{
  assert.equal(membershipUpdateConfirmed('pro',[],'free',new Set()),true);
  assert.equal(membershipUpdateConfirmed('pro',[{order_id:'renewal',status:'paid'}],'pro',new Set(['old'])),true);
  assert.equal(membershipUpdateConfirmed('pro',[{order_id:'old',status:'paid'}],'pro',new Set(['old'])),false);
});
