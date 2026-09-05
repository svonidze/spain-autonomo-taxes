import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test,expect} from 'vitest';
import {legacyCore} from '../src/core/legacy.ts';
import {item,preview,result,postItems} from '../src/features/posting/normalize.ts';
import {postingState,markStale,clearRefresh,refreshToken} from '../src/features/posting/state.ts';
test('posting normalization preserves nested aliases, zero amounts and partial-result identities',()=>{
  const context=vm.createContext({AutonomoCore:legacyCore,Intl,Date,setTimeout,clearTimeout,URLSearchParams,console});vm.runInContext(readFileSync('src/autonomo_taxes/web_ui/app.js','utf8'),context);vm.runInContext("state.period='2026-Q3'",context);
  const samples=[{review_id:'transaction:synthetic-one',row_version:7,amount_eur:'121.00',reasons:[{code:'cleanup_apply',message:'Synthetic cleanup'}],cleanup_applies:true},
    {result:{transaction_id:'synthetic-two',expected_row_version:3,amount_eur:'0.00'},blocked:true,blockers:[{code:'future',message:'Synthetic blocker'}]},
    {transaction_id:'synthetic-three',outcome:'posted',reason_code:'posted_cleanup_failed',effective_amount_eur_minor:-12100,cleanup_blocked:true},
    {transaction_id:'synthetic-zero',expected_row_version:0,amount_eur:0,ready_to_post:true}];
  for(const sample of samples){context.raw=sample;expect(item(sample)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('normalizePostingItem(raw)',context))));}
  for(const value of [{period:'2026-Q3',period_status:'open',ready:samples.slice(0,1),deferred:samples.slice(1,2),blocked:[],summary:{ready_count:1,approved_count:3,cleanup_count:1}},{items:samples,as_of:'2026-09-01'},{}]){
    context.raw=value;expect(preview(value,'2026-Q3')).toEqual(JSON.parse(JSON.stringify(vm.runInContext('normalizePostingPreview(raw)',context))));
  }
  const ready=samples.map(item);context.ready=ready;
  for(const value of [{status:'partial',results:[{transaction_id:'synthetic-one',outcome:'posted'},{transaction_id:'synthetic-two',outcome:'blocked',blockers:[{code:'stale_row_version'}]}],summary:{posted_count:1}},{status:'completed',processed:2},{status:'interrupted',interrupted:true,posted_count:0}]){
    context.raw=value;expect(result(value,ready)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('normalizePostingResult(raw,ready)',context))));
  }
  expect(postItems(ready)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('buildPostReadyItems(ready)',context))));
});
test('an earlier calculation cannot clear a newer or different-period stale marker',()=>{
  postingState.stale.clear();const first=markStale('2026-Q2'),second=markStale('2026-Q2','Synthetic later write');markStale('2026-Q3');
  clearRefresh('2026-Q2',first);expect(refreshToken('2026-Q2')).toBe(second);clearRefresh('2026-Q3',second);expect(refreshToken('2026-Q3')).toBeDefined();clearRefresh('2026-Q2',second);expect(refreshToken('2026-Q2')).toBeUndefined();postingState.stale.clear();
});
