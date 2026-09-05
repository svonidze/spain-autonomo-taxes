import {formatMessage,messageIds} from '../../core/i18n.ts';
import type {PostingItem,PostingPreview,PostingResult,PostingSummary} from './model.ts';
const object=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'?value as Record<string,unknown>:{};
const array=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const text=(value:unknown)=>String(value||'').trim();
export function integer(value:unknown,fallback=0){const number=Number.parseInt(String(value??''),10);return Number.isFinite(number)?number:fallback;}
function unique(values:string[]){return values.filter((value,index)=>!!value&&values.indexOf(value)===index);}
function transactionId(value:string){return value.startsWith('transaction:')?value.slice('transaction:'.length):'';}
export function statusLabel(value:string){const key=`statuses.labels.${value}`;return messageIds.includes(key)?formatMessage(key):value.replaceAll('_',' ');}
export function reasons(value:unknown){const raw=object(value);return unique([...array(raw.reasons),...array(raw.blockers),...array(raw.blocking_issues)].map(value=>typeof value==='object'&&value?text(object(value).message||object(value).reason||object(value).code):text(value)));}
export function item(value:unknown):PostingItem{
  const raw=object(value),detail=object(raw.result),reviewId=text(raw.review_id||detail.review_id),id=text(raw.transaction_id||detail.transaction_id||transactionId(reviewId));
  const expected=[raw.expected_row_version,detail.expected_row_version,raw.row_version,detail.row_version].find(value=>Number.isInteger(value)) as number|undefined;
  const minor=[raw.effective_amount_eur_minor,detail.effective_amount_eur_minor].find(value=>Number.isInteger(value)) as number|undefined;
  const messages=unique([...reasons(detail),...reasons(raw)]),reasonCode=text(raw.reason_code||detail.reason_code);
  return {reviewId,transactionId:id,expectedRowVersion:expected??null,transactionDate:text(raw.transaction_date||detail.transaction_date),entryType:text(raw.entry_type||detail.entry_type),description:text(raw.description||detail.description||raw.counterparty_name||detail.counterparty_name),
    amountEur:raw.amount_eur==null&&detail.amount_eur==null?minor==null?'':String(minor/100):String(raw.amount_eur??detail.amount_eur).trim(),
    rowStatus:text(raw.outcome||raw.status||raw.state||detail.outcome||detail.status||raw.preview_bucket||'unknown'),previewBucket:(raw.preview_bucket||detail.preview_bucket||null) as string|null,postingDeferredUntil:(raw.posting_deferred_until||detail.posting_deferred_until||null) as string|null,
    structuredReasons:[...array(raw.blockers),...array(detail.blockers),...array(raw.blocking_issues)].filter(value=>value&&typeof value==='object') as Record<string,unknown>[],documentId:(raw.document_id||detail.document_id||null) as string|null,
    message:unique([text(raw.message||detail.message||raw.reason||detail.reason),reasonCode,reasonCode==='posted_cleanup_failed'?statusLabel('posted_cleanup_failed'):'',...messages]).join(' · '),reasons:messages,
    cleanupApplied:Boolean(raw.cleanup_applies??detail.cleanup_applies??object(raw.cleanup).applicable??object(detail.cleanup).applicable),cleanupBlocked:Boolean(raw.cleanup_blocked??detail.cleanup_blocked??object(raw.cleanup).blocking??object(detail.cleanup).blocking),readyToPost:Boolean(raw.ready_to_post)};
}
export const items=(value:unknown)=>array(value).map(item);
export function summary(value:unknown,collections:{ready?:PostingItem[];deferred?:PostingItem[];blocked?:PostingItem[]}={}):PostingSummary{
  const raw=object(value),readyCount=integer(raw.ready_count,integer(raw.ready_to_post,collections.ready?.length||0)),deferredCount=integer(raw.deferred_count,collections.deferred?.length||0),blockedCount=integer(raw.blocked_count,collections.blocked?.length||0),rows=[...(collections.ready||[]),...(collections.deferred||[]),...(collections.blocked||[])];
  const minor=integer(raw.ready_total_eur_minor,integer(raw.ready_income_eur_minor)+integer(raw.ready_expense_eur_minor));
  return{readyCount,deferredCount,blockedCount,approvedCount:integer(raw.approved_count,readyCount+deferredCount+blockedCount),readyTotalEur:String(raw.ready_total_eur||raw.ready_amount_eur||(minor/100).toFixed(2)),cleanupCount:integer(raw.cleanup_count,rows.filter(row=>row.cleanupApplied).length),cleanupBlockedCount:integer(raw.cleanup_blocked_count,rows.filter(row=>row.cleanupBlocked).length)};
}
export function preview(value:unknown,period:string):PostingPreview{
  const raw=object(value),legacy=items(raw.items),ready=Array.isArray(raw.ready)?items(raw.ready):legacy.filter(row=>row.readyToPost),deferred=items(raw.deferred),blocked=items(raw.blocked);
  return{period:text(raw.period||period),periodStatus:text(raw.period_status||raw.status||'open')||'open',generatedAt:(raw.generated_at||raw.as_of||null) as string|null,asOf:(raw.as_of||null) as string|null,summary:summary(raw.summary||raw.posting_summary,{ready,deferred,blocked}),ready,deferred,blocked};
}
export function result(value:unknown,ready:PostingItem[]):PostingResult{
  const raw=object(value),known=new Map<string,PostingItem>();for(const row of ready){if(row.reviewId)known.set(row.reviewId,row);if(row.transactionId)known.set(row.transactionId,row);}
  const source=array(raw.results),rows=source.length?source.map(value=>{const row=item(value),fallback=known.get(row.reviewId)||known.get(row.transactionId);return{...fallback,...row,transactionDate:row.transactionDate||fallback?.transactionDate||'',entryType:row.entryType||fallback?.entryType||'',description:row.description||fallback?.description||'',amountEur:row.amountEur||fallback?.amountEur||'',rowStatus:row.rowStatus||String(raw.status||'unknown')};}):ready.map(row=>({...row,rowStatus:['ok','completed'].includes(String(raw.status||''))?'posted':String(raw.status||'unknown'),message:text(raw.message)}));
  const counts=object(raw.summary),postedCount=integer(counts.posted_count,integer(counts.posted,integer(raw.posted_count,integer(raw.posted,integer(raw.processed,rows.filter(row=>['posted','included_in_snapshot','ok','completed'].includes(row.rowStatus)).length)))));
  return{status:String(raw.status||'unknown'),interrupted:Boolean(raw.interrupted)||String(raw.status||'')==='interrupted',summary:{postedCount},results:rows};
}
export const postItems=(rows:PostingItem[])=>rows.map(row=>({transaction_id:row.transactionId||transactionId(row.reviewId),expected_row_version:row.expectedRowVersion}));
export const helpContext=(row:PostingItem)=>({domain:'transaction',title:row.description,state:row.previewBucket||row.rowStatus||'unknown',posting:{preview_bucket:row.previewBucket,posting_deferred_until:row.postingDeferredUntil,blockers:row.structuredReasons||[]},reasons:[],actions:row.transactionId?[{kind:'review',transaction_id:row.transactionId}]:[],facts:{transaction_date:row.transactionDate}});
