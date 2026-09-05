import {createApp, defineComponent, h, shallowRef, type Component} from 'vue';

export interface ViewHost<C> {updateContext(context: C): void; dispose(): void;}
/** A host owns exactly one Vue root; route replacement always disposes it first. */
export function mountView<C extends object>(root: HTMLElement, component: Component, initial: C): ViewHost<C> {
  const context = shallowRef(initial);
  const app = createApp(defineComponent({setup: () => () => h(component, {context: context.value})}));
  root.replaceChildren();
  root.dataset.vueOwned = '';
  app.mount(root);
  let active = true;
  return {
    updateContext(value) {if (active) context.value = value;},
    dispose() {if (active) {active = false; app.unmount(); delete root.dataset.vueOwned;}},
  };
}
