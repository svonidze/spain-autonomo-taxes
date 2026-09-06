import {markRaw,nextTick,onBeforeUnmount,onMounted,ref,shallowRef,watch,type Component} from 'vue';
import {router} from './router.ts';
import {parseRoute,routeUrl,knownPeriod,safeReturnUrl,copyTarget,currentQuarter,type Period,type ScreenRoute} from './routes.ts';
import {request,refreshCalculation} from './request.ts';
import {connectHelp,shellHelp} from './help-history.ts';
import {getLocale,setLocale,loadLocale,storeLocale,message,localeRegistry,formatMessage,type UiMessage} from '../core/i18n.ts';
import {errorMessage} from '../core/error-message.ts';
import {useLocale} from '../vue/locale.ts';
import type IntakeDialog from '../features/intake/IntakeDialog.vue';
import {currencies,type IntakeResult,type IntakeServices} from '../features/intake/model.ts';
import {buildIncomeCopy} from '../features/transactions/income-copy.ts';
import {copyable,type TransactionRow,type TransactionListContext} from '../features/transactions/model.ts';
import {refreshToken,clearRefresh} from '../features/posting/state.ts';
import type {ContactsContext} from '../features/contacts/model.ts';
import type {ExpenseDetailContext} from '../features/expense-detail/model.ts';
import type {ReviewDetailContext} from '../features/review/guided-model.ts';
import type {ReviewOverviewContext} from '../features/posting/model.ts';
import type {OverviewContext} from '../features/overview/model.ts';
import type {SettingsContext} from '../features/settings/model.ts';
import DashboardView from '../features/overview/DashboardView.vue';
import AssetsView from '../features/overview/AssetsView.vue';
import TaxesView from '../features/overview/TaxesView.vue';
import ContactsView from '../features/contacts/ContactsView.vue';
import ExpenseDetail from '../features/expense-detail/ExpenseDetail.vue';
import ReviewDetail from '../features/review/ReviewDetailView.vue';
import ReviewOverview from '../features/posting/ReviewOverviewView.vue';
import TransactionLists from '../features/transactions/TransactionListsView.vue';
import SettingsView from '../features/settings/SettingsView.vue';
interface Bootstrap {periods:Period[];default_period:string;profile_name:string;intake_enabled:boolean;}
export interface ViewGuards {canLeave?():boolean;isDirty?():boolean;isBusy?():boolean;}
interface Position {x:number;y:number;tableX:number;id:string|null;}
export function useShell(){
  const {locale}=useLocale();setLocale(loadLocale(()=>localStorage));
  const bootstrap=shallowRef<Bootstrap>(),period=ref(''),route=shallowRef<ScreenRoute|null>(),screen=shallowRef<{component:Component;context:object}>();
  const reviewTab=ref<'queue'|'posting'|'documents'>('queue');
  const view=ref<ViewGuards>(),intake=ref<InstanceType<typeof IntakeDialog>>();
  const loading=ref(true),failure=shallowRef<unknown>(),busy=ref(false),resolved=ref(false),revision=ref(0);
  const toast=shallowRef<{value:UiMessage|string;error:boolean}>();let toastTimer:ReturnType<typeof setTimeout>|undefined;
  let active=true,generation=0,contactGlobal='',contactPosition:Position|undefined;
  let presentation:{from:string;to:string}|undefined;
  const locationKey=()=>window.location.pathname+window.location.search;
  const canLeave=()=>view.value?.canLeave?.()??true;
  const notify=(value:UiMessage|string,error=false)=>{toast.value={value,error};clearTimeout(toastTimer);if(!error)toastTimer=setTimeout(()=>{toast.value=undefined;},3200);};
  function rememberPosition(){if(route.value?.view!=='contacts')return;const wrapper=document.querySelector<HTMLElement>('#contacts-list-wrap');if(!wrapper)return;contactPosition={x:window.scrollX,y:window.scrollY,tableX:wrapper.scrollLeft,id:document.activeElement?.closest<HTMLElement>('[data-counterparty-row]')?.dataset.counterpartyRow||null};}
  function restorePosition(){if(!contactPosition)return;const wrapper=document.querySelector<HTMLElement>('#contacts-list-wrap');if(wrapper)wrapper.scrollLeft=contactPosition.tableX;[...document.querySelectorAll<HTMLElement>('[data-counterparty-row]')].find(row=>row.dataset.counterpartyRow===contactPosition!.id)?.querySelector('a')?.focus({preventScroll:true});window.scrollTo({left:contactPosition.x,top:contactPosition.y,behavior:'auto'});}
  function navigationState(){const fromContact=route.value?.view==='contact-detail'||route.value?.view==='expense-detail';return {contactGlobalPeriod:fromContact&&contactGlobal?contactGlobal:period.value,accountingHelp:null};}
  function navigate(url:string,replace=false){const target=new URL(url,window.location.origin);if(target.origin!==window.location.origin)return;void router[replace?'replace':'push']({path:target.pathname,query:Object.fromEntries(target.searchParams),hash:target.hash,state:navigationState()});}
  async function replacePresentation(url:string){
    const from=router.currentRoute.value.fullPath,to=router.resolve(url).fullPath;const token={from,to};presentation=token;
    try{await router.replace({path:router.resolve(url).path,query:router.resolve(url).query,hash:router.resolve(url).hash,state:{contactGlobalPeriod:contactGlobal||period.value}});}finally{if(presentation===token)presentation=undefined;}
  }
  const removeGuard=router.beforeEach((to,from)=>{
    if(to.fullPath===from.fullPath||(presentation?.from===from.fullPath&&presentation.to===to.fullPath))return true;
    if(!canLeave())return false;rememberPosition();return true;
  });
  const removeAfter=router.afterEach((to,from,error)=>{
    if(error)return;
    if(to.fullPath===from.fullPath){shellHelp().handlePopState();return;}
    if(presentation?.from===from.fullPath&&presentation.to===to.fullPath)return;
    if(bootstrap.value)void showRoute();
  });
  function changeLocale(value:string){
    if(route.value?.view==='settings'&&!canLeave())return;
    setLocale(value);storeLocale(()=>localStorage,value);
  }
  watch(locale,value=>{document.documentElement.lang=value;document.documentElement.dir=localeRegistry.find(item=>item.code===value)?.direction||'ltr';document.title=formatMessage('app.title',{},value);shellHelp().setLocale(value);},{immediate:true});
  function copy(row:TransactionRow,sourcePeriod:string){const target=copyTarget(bootstrap.value?.periods||[],!!bootstrap.value?.intake_enabled);if(!copyable(row,target)||!target)return;const result=buildIncomeCopy(row,{sourcePeriodKey:sourcePeriod,targetPeriodKey:target,targetIsCurrentQuarter:target===currentQuarter(),currencyOptions:currencies});void intake.value?.open('income_invoice',{targetPeriodKey:target,...result});void intake.value?.focusDocumentNumber();}
  function add(){if(!bootstrap.value?.intake_enabled||!period.value)return;void intake.value?.open(route.value?.view==='income'?'income_invoice':'expense_invoice',{targetPeriodKey:period.value});}
  async function showRoute(){
    const data=bootstrap.value;if(!data)return;const version=++generation;let current=router.currentRoute.value;
    if(current.path==='/'){navigate(routeUrl('dashboard',period.value),true);return;}
    const next=parseRoute(current.path);const query=new URL(current.fullPath,window.location.origin).searchParams;
    if(next?.view==='contact-detail'){
      const saved=window.history.state?.contactGlobalPeriod;
      if(data.periods.some(row=>row.period_key===saved))period.value=saved;
      contactGlobal=period.value;
    }else if(next&&!next.id&&next.view!=='contacts'&&next.view!=='settings')period.value=knownPeriod(query.toString(),data.periods)||period.value||data.default_period;
    if(next&&!next.id&&!['contacts','settings'].includes(next.view)&&period.value)query.set('period',period.value);
    const requestedTab=query.get('tab');
    const tab=requestedTab==='posting'||requestedTab==='documents'?requestedTab:'queue';
    if(next?.view==='review'&&!next.id&&tab!=='queue')query.set('tab',tab);else query.delete('tab');
    const canonical=current.path+(query.size?`?${query}`:'')+current.hash;
    if(canonical!==current.fullPath){await replacePresentation(canonical);current=router.currentRoute.value;}
    if(!active||generation!==version||router.currentRoute.value.fullPath!==router.resolve(canonical).fullPath)return;
    if(next?.view==='expense-detail')contactGlobal=query.get('returnTo')?.startsWith('/contacts/')?String(window.history.state?.contactGlobalPeriod||contactGlobal):'';
    else if(next?.view!=='contact-detail')contactGlobal='';
    route.value=next;if(next?.view==='review'&&!next.id)reviewTab.value=tab;resolved.value=false;failure.value=undefined;loading.value=true;
    shellHelp().beforeRender();screen.value=undefined;await nextTick();if(!active||generation!==version)return;
    if(!next){loading.value=false;return;}
    const sourcePeriod=period.value,target=copyTarget(data.periods,data.intake_enabled);
    const services={request,navigate:(url:string)=>navigate(url)};
    const settled=()=>{if(active&&version===generation)loading.value=false;};
    const owns=()=>active&&version===generation;
    const returnTo=query.get('returnTo');
    const resolvePeriod=(value:Period)=>{
      if(!owns())return;
      period.value=value.period_key;resolved.value=true;
      if(!data.periods.some(row=>row.period_key===value.period_key))bootstrap.value={...data,periods:[...data.periods,value]};
      void replacePresentation(routeUrl(next.view,value.period_key,{id:next.id,returnTo:returnTo?safeReturnUrl(returnTo,value.period_key,bootstrap.value!.periods,next.view==='review'?'review':'expenses'):undefined}));
    };
    const mount=(component:Component,context:object)=>{revision.value++;screen.value={component:markRaw(component),context};};
    if(next.view==='settings')mount(SettingsView,{services,settled,onProfile(name){if(owns())bootstrap.value={...bootstrap.value!,profile_name:name};},onLocale:changeLocale,reload(){if(canLeave())void showRoute();}} satisfies SettingsContext);
    else if(next.view==='contact-detail'||next.view==='contacts')mount(ContactsView,{contactId:next.id||null,period:query.get('period')||'',services,settled,detailResolved(){},notify,chartPeriod:sourcePeriod,restorePosition,rememberPosition} satisfies ContactsContext);
    else if(next.view==='expense-detail')mount(ExpenseDetail,{transactionId:next.id!,returnUrl:safeReturnUrl(returnTo,sourcePeriod,data.periods),services,settled,resolved(value){resolvePeriod(value.period);return {returnUrl:safeReturnUrl(returnTo,value.period.period_key,bootstrap.value!.periods),wrongTypeUrl:routeUrl(value.transaction.entry_type==='income'?'income':'dashboard',value.period.period_key)};}} satisfies ExpenseDetailContext);
    else if(next.view==='review'&&next.id)mount(ReviewDetail,{transactionId:next.id,period:sourcePeriod,returnUrl:safeReturnUrl(returnTo,sourcePeriod,data.periods,'review'),knownPeriods:data.periods.map(row=>row.period_key),services,settled,notify,resolved(value){resolvePeriod(value);return safeReturnUrl(returnTo,value.period_key,bootstrap.value!.periods,'review');},complete(outcome){if(!owns())return;notify(message(outcome==='approve'?'review.confirmSuccess':'review.rejectSuccess'));navigate(routeUrl('review',period.value,{tab:reviewTab.value}));},posted(id,value,replace=true){if(owns())navigate(routeUrl('expense-detail',value,{id,returnTo:returnTo||undefined}),replace);}} satisfies ReviewDetailContext);
    else if(next.view==='review')mount(ReviewOverview,{period:sourcePeriod,tab,services,settled,notify,selectTab(value){navigate(routeUrl('review',sourcePeriod,{tab:value}));},refreshCalculation} satisfies ReviewOverviewContext);
    else if(next.view==='expenses'||next.view==='income')mount(TransactionLists,{kind:next.view==='expenses'?'expense':'income',period:sourcePeriod,query:next.view==='expenses'?query.get('q')||'':'',copyTarget:target,services,settled,queryChanged(value){if(owns()&&next.view==='expenses')void replacePresentation(routeUrl('expenses',sourcePeriod,{q:value}));},openIntake:add,copy:row=>copy(row,sourcePeriod)} satisfies TransactionListContext);
    else mount({dashboard:DashboardView,assets:AssetsView,taxes:TaxesView}[next.view],{view:next.view,period:sourcePeriod,copyTarget:target,services,settled,notify,refreshCalculation:()=>refreshCalculation(sourcePeriod),copy:row=>copy(row,sourcePeriod)} satisfies OverviewContext);
  }
  async function refresh(){if(!canLeave()||busy.value)return;const version=generation,captured=period.value;busy.value=true;try{if(route.value?.view!=='contact-detail'&&route.value?.view!=='expense-detail'){const token=refreshToken(captured);await refreshCalculation(captured);clearRefresh(captured,token);if(version===generation)notify(message('refresh.done',{period:captured}));}if(version===generation)await showRoute();}catch(error){if(version===generation)notify(errorMessage(error,getLocale()),true);}finally{busy.value=false;}}
  async function init(){loading.value=true;failure.value=undefined;try{const data=await request('/api/bootstrap') as Bootstrap;if(!active)return;bootstrap.value=data;period.value=data.default_period;await router.isReady();if(window.history.state?.accountingHelp)await router.replace({path:router.currentRoute.value.path,query:router.currentRoute.value.query,force:true,state:{accountingHelp:null}});await showRoute();}catch(error){if(active){failure.value=error;loading.value=false;}}}
  function selectPeriod(value:string){navigate(routeUrl(route.value?.view||'dashboard',value,{tab:reviewTab.value}),true);}
  function accepted(result:IntakeResult){if(result.kind==='expense_invoice'&&result.transaction_id)navigate(routeUrl('review',result.period,{id:result.transaction_id}));else void showRoute();}
  const intakeServices:IntakeServices={request,notify,locationKey,accepted};
  const beforeUnload=(event:BeforeUnloadEvent)=>{if(view.value?.isDirty?.()||view.value?.isBusy?.()){event.preventDefault();event.returnValue='';}};
  onMounted(()=>{connectHelp(url=>navigate(url));window.addEventListener('beforeunload',beforeUnload);void init();});
  onBeforeUnmount(()=>{active=false;generation++;removeGuard();removeAfter();clearTimeout(toastTimer);window.removeEventListener('beforeunload',beforeUnload);});
  return {bootstrap,period,reviewTab,route,screen,view,intake,loading,failure,busy,resolved,revision,toast,locale,notify,changeLocale,navigate,add,refresh,init,selectPeriod,intakeServices};
}
