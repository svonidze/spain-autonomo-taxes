import type {ViewServices} from '../../vue/services.ts';
import type {UiMessage} from '../../core/i18n.ts';
export type IntakeKind='expense_invoice'|'income_invoice';
export type IntakeSource='upload'|'google_drive';
export const currencies=['EUR','USD','GBP'];
export const draftFields=['issued_on','counterparty_name','document_number','currency','gross','taxable_base','vat','drive_url'] as const;
export type DraftValues=Partial<Record<typeof draftFields[number],string>>;
export interface IntakeOptions {targetPeriodKey:string;prefill?:DraftValues|null;noticeLines?:(UiMessage|string)[];}
export interface IntakeResult {system_marker?:string;period:string;kind:IntakeKind;transaction_id?:string;}
export interface IntakeServices {request:ViewServices['request'];notify(value:UiMessage|string,error?:boolean):void;locationKey():string;accepted(result:IntakeResult):void;}
export interface Folder {id:string;name:string;}
export interface PickerConfig {enabled:boolean;developer_key?:string;app_id?:string;access_token?:string;}
export const draftKey='autonomo.intake-draft';
const storage=()=>localStorage;
export function emptyDraft(values:DraftValues){const keys=Object.keys(values);return keys.length===0||(keys.length===1&&values.currency==='EUR');}
export function readValues(elements:HTMLFormControlsCollection):DraftValues{
  const values:DraftValues={};for(const key of draftFields){const element=elements.namedItem(key) as HTMLInputElement|null;if(element&&typeof element.value==='string'&&element.value!=='')values[key]=element.value;}return values;
}
export function rawDraft(store:()=>Pick<Storage,'getItem'>=storage):string|null|undefined{try{return store().getItem(draftKey);}catch{return undefined;}}
export function saveDraft(values:DraftValues,store:()=>Pick<Storage,'setItem'|'removeItem'>=storage){try{if(emptyDraft(values))store().removeItem(draftKey);else store().setItem(draftKey,JSON.stringify({schema:1,values}));}catch{/* Storage is optional. */}}
export function loadDraft(store:()=>Pick<Storage,'getItem'>=storage):DraftValues|null{
  try{const parsed=JSON.parse(store().getItem(draftKey)||'null');if(!parsed||parsed.schema!==1||!parsed.values||typeof parsed.values!=='object')return null;
    const values:DraftValues={};for(const key of draftFields)if(Object.hasOwn(parsed.values,key)&&typeof parsed.values[key]==='string')values[key]=parsed.values[key];return emptyDraft(values)?null:values;
  }catch{return null;}
}
export function clearUnchangedDraft(expected:string|null|undefined,store:()=>Pick<Storage,'getItem'|'removeItem'>=storage){try{if(store().getItem(draftKey)===expected)store().removeItem(draftKey);}catch{/* Preserve any newer or unavailable draft. */}}
export function isGoogleDriveUrl(value:string){try{const url=new URL(value);if(url.protocol!=='https:'||url.username||url.password||url.port)return false;const parts=url.pathname.split('/').filter(Boolean),id=/^[A-Za-z0-9_-]{10,256}$/;if(url.hostname==='drive.google.com')return(parts[0]==='file'&&parts[1]==='d'&&id.test(parts[2]||''))||(['open','uc'].includes(parts[0])&&id.test(url.searchParams.get('id')||''));return url.hostname==='docs.google.com'&&['document','spreadsheets','presentation','drawings'].includes(parts[0])&&parts[1]==='d'&&id.test(parts[2]||'');}catch{return false;}}
export function intakeRequest(data:FormData,source:IntakeSource,url:string,folder?:Folder){
  if(data.get('kind')==='expense_invoice')data.set('defer_counterparty','1');
  if(source==='google_drive'){data.delete('file');const fields=Object.fromEntries(data.entries());delete fields.drive_url;return{url:'/api/intake/google-drive',options:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({fields,drive_url:url.trim()})}};}
  data.delete('drive_url');if(folder)data.set('google_folder_id',folder.id);return{url:'/api/intake',options:{method:'POST',body:data}};
}

export function intakeResult(value:unknown,expected:{kind:IntakeKind;period:string}):IntakeResult {
  const result=value as IntakeResult|null;
  if(!result||!['expense_invoice','income_invoice'].includes(result.kind)||result.kind!==expected.kind||result.period!==expected.period||!/^\d{4}-Q[1-4]$/.test(result.period))throw new Error('intake.invalidResponse');
  return result;
}
