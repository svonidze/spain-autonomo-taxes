import {router} from './router.ts';
import {helpAdapter,type HelpAdapter} from '../vue/help.ts';
interface HistoryAdapter {push(id:string):void;back():void;navigate(url:string):void;}
interface ShellHelp extends HelpAdapter {setLocale(locale:string):void;beforeRender():void;handlePopState():boolean;setHistoryAdapter(adapter:HistoryAdapter):void;}
// Temporary boundary for the old read-only help presenter. Its registry becomes
// a typed module at finalization; all actual navigation already belongs to Router.
export const shellHelp=()=>helpAdapter() as ShellHelp;
export function connectHelp(navigate:(url:string)=>void){shellHelp().setHistoryAdapter({
  push(id){const current=router.currentRoute.value;void router.push({path:current.path,query:current.query,hash:current.hash,force:true,state:{accountingHelp:id,contactGlobalPeriod:window.history.state?.contactGlobalPeriod??null}});},
  back:()=>router.back(),navigate,
});}
