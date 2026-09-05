import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test,expect} from 'vitest';
import {legacyCore} from '../src/core/legacy.ts';
import {answers,autoResolve,confirmFx,legacyErrorTarget,merge} from '../src/features/review/guided-helpers.ts';
import {guidedFixture} from './fixtures/review.ts';
import type {FxChoice} from '../src/features/review/guided-model.ts';
test('guided merge, answers, FX and error routing preserve the legacy oracle',()=>{
  const context=vm.createContext({AutonomoCore:legacyCore,Intl,Date,setTimeout,clearTimeout,URLSearchParams,console});
  vm.runInContext(readFileSync('src/autonomo_taxes/web_ui/app.js','utf8'),context);
  const packet=guidedFixture().packet;packet.decision.business_purpose='Synthetic purpose';packet.decision.reason='Synthetic reason';packet.decision.issue_resolutions=[{issue_id:'synthetic-issue',action:null,reason:''}];packet.state.issues=[{validation_issue_id:'synthetic-issue',issue_code:'synthetic_code'}];
  context.packet=packet;context.coverage={synthetic_code:['business_purpose','reason']};context.choice={mode:'settlement',rate:'0.92',rateDate:'2026-07-01',sourceReference:'Synthetic evidence'};
  expect(answers(packet.decision,packet.state,context.choice)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('questionAnswerMap(packet.decision, packet.state, choice)',context))));
  for(const choice of [null,{mode:'ecb'},{mode:'settlement',rate:'',rateDate:'',sourceReference:''},context.choice] as (FxChoice|null)[]){context.choice=choice;context.suggestion={status:'exact',rate_date:'2026-07-01',eur_per_unit:'0.92'};expect(confirmFx(choice,context.suggestion)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('buildConfirmFxSpec(choice,suggestion)',context))));}
  const current=JSON.parse(JSON.stringify(packet));autoResolve(current,context.coverage,answers(current.decision,current.state,null),'ru');vm.runInContext('autoResolveCoveredIssues(packet,coverage,questionAnswerMap(packet.decision,packet.state,null))',context);expect(current.decision).toEqual(JSON.parse(JSON.stringify(packet.decision)));
  for(const error of ['business_purpose is required','vat_investment_good','missing counterparty country','fx rate missing','reason is required','Synthetic unknown'])expect(legacyErrorTarget(error)).toBe(vm.runInContext(`mapConfirmErrorToQuestion(${JSON.stringify(error)})`,context));
  context.base={tax_treatment:{tax_code:'domestic_output',vat_minor:0},counterparty_changes:{country_code:'ZZ'}};context.override={counterparty_changes:{country_code:'ES'},issue_resolutions:[{action:'resolve'}]};expect(merge(context.base,context.override)).toEqual(JSON.parse(JSON.stringify(vm.runInContext('deepMerge(base,override)',context))));
});
