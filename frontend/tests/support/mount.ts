import { createApp, defineComponent, h, shallowRef, type Component } from 'vue';

interface ViewGuards {
  canLeave?(): boolean;
  isDirty?(): boolean;
  isBusy?(): boolean;
}
export interface ViewHost<C> {
  updateContext(context: C): boolean;
  dispose(): void;
  canLeave(): boolean;
  isDirty(): boolean;
  isBusy(): boolean;
}
/** A host owns exactly one Vue root; route replacement always disposes it first. */
export function mountView<C extends object>(
  root: HTMLElement,
  component: Component,
  initial: C,
): ViewHost<C> {
  const context = shallowRef(initial);
  const instance = shallowRef<ViewGuards>();
  const app = createApp(
    defineComponent({ setup: () => () => h(component, { context: context.value, ref: instance }) }),
  );
  root.replaceChildren();
  root.dataset.vueOwned = '';
  app.mount(root);
  let active = true;
  return {
    canLeave: () => instance.value?.canLeave?.() ?? true,
    isDirty: () => instance.value?.isDirty?.() ?? false,
    isBusy: () => instance.value?.isBusy?.() ?? false,
    updateContext(value) {
      if (!active) return false;
      if (value === context.value) return true;
      if (instance.value?.canLeave && !instance.value.canLeave()) return false;
      context.value = value;
      return true;
    },
    dispose() {
      if (active) {
        active = false;
        app.unmount();
        delete root.dataset.vueOwned;
      }
    },
  };
}
