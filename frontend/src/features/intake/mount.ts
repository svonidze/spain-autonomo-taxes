import {createApp} from 'vue';
import IntakeDialog from './IntakeDialog.vue';
import type {IntakeServices} from './model.ts';
export function mountIntake(services:IntakeServices){
  const root=document.createElement('div');document.body.append(root);
  const app=createApp(IntakeDialog,{services}),instance=app.mount(root) as InstanceType<typeof IntakeDialog>;
  return{open:instance.open,close:instance.close,focusDocumentNumber:instance.focusDocumentNumber,isBusy:instance.isBusy,dispose(){app.unmount();root.remove();}};
}
