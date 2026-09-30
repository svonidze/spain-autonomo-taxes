<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../vue/locale.ts';
import { captureFocus, restoreFocus } from '../core/focus.ts';
import type { ViewServices } from '../vue/services.ts';
import { createChartBuilders, type ChartKind } from './builders.ts';
import type { Analytics, ChartSpec } from './types.ts';
import { renderCartesian, renderHorizontalBars, renderBullet } from './renderer.js';
const props = defineProps<{
  kind: ChartKind;
  period: string;
  request?: ViewServices['request'];
  analytics?: Analytics;
  failed?: boolean;
  revision?: number;
}>();
const { locale, t } = useLocale();
const host = ref<HTMLElement>(),
  dialog = ref<HTMLDialogElement>(),
  expandedHost = ref<HTMLElement>();
const source = shallowRef<Analytics>(),
  failure = ref(false),
  opened = ref(false);
let active = true,
  requestVersion = 0,
  opener: HTMLElement | undefined,
  observer: ResizeObserver | undefined;
const horizontal: ChartKind[] = ['expenseStructure', 'reviewAging', 'counterparty'];
const render = computed(() =>
  props.kind === 'reserve'
    ? renderBullet
    : horizontal.includes(props.kind)
      ? renderHorizontalBars
      : renderCartesian,
);
const spec = computed(() =>
  source.value ? createChartBuilders(locale.value)[props.kind](source.value) : null,
);
function paint(container: HTMLElement | undefined, expanded = false) {
  if (!container || !spec.value) return;
  const focus = captureFocus(container),
    details = container.querySelector('details');
  const width = Math.round(container.getBoundingClientRect().width) - 28;
  const value: ChartSpec = { ...spec.value, ...(width > 0 ? { width } : {}) };
  if (!expanded) value.expandAction = { label: t('charts.expand'), handler: open };
  render.value(container, value);
  if (!expanded && opened.value)
    opener = container.querySelector<HTMLElement>('.chart-expand-button') || undefined;
  const next = container.querySelector('details');
  if (next && details) next.open = details.open;
  restoreFocus(container, focus);
}
async function open(trigger: HTMLElement) {
  opener = trigger;
  opened.value = true;
  await nextTick();
  if (!active || !dialog.value) return;
  if (!dialog.value.open) dialog.value.showModal();
  paint(expandedHost.value, true);
  dialog.value.querySelector('button')?.focus();
}
function close() {
  dialog.value?.close();
}
function closed() {
  opened.value = false;
  if (opener?.isConnected) opener.focus({ preventScroll: true });
  opener = undefined;
}
async function load() {
  const version = ++requestVersion;
  failure.value = !!props.failed;
  if (props.analytics) {
    source.value = props.analytics;
    return;
  }
  source.value = undefined;
  if (!props.request || props.failed) return;
  try {
    const result = (await props.request(
      `/api/analytics?period=${encodeURIComponent(props.period)}`,
    )) as Analytics;
    if (active && version === requestVersion) source.value = result;
  } catch {
    if (active && version === requestVersion) failure.value = true;
  }
}
watch(
  () => [props.period, props.analytics, props.failed, props.request, props.revision],
  () => void load(),
  { immediate: true },
);
watch(
  spec,
  async () => {
    await nextTick();
    if (active) {
      paint(host.value);
      if (opened.value) paint(expandedHost.value, true);
    }
  },
  { flush: 'post' },
);
onMounted(() => {
  const widths = new WeakMap<Element, number>();
  observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      const width = entry.contentRect.width;
      if (widths.get(entry.target) !== width) {
        widths.set(entry.target, width);
        paint(entry.target as HTMLElement, entry.target === expandedHost.value);
      }
    }
  });
  if (host.value) observer.observe(host.value);
  if (expandedHost.value) observer.observe(expandedHost.value);
  paint(host.value);
});
onBeforeUnmount(() => {
  active = false;
  requestVersion++;
  observer?.disconnect();
  dialog.value?.close();
});
</script>
<template>
  <div class="chart-slot">
    <div v-if="failure || failed" class="empty-state chart-empty-state">
      {{ t('charts.loadError') }}
    </div>
    <div ref="host" v-show="!failure && !failed"></div>
    <Teleport to="body"
      ><dialog
        ref="dialog"
        :id="`vue-chart-${kind}`"
        class="intake-dialog chart-dialog"
        :aria-labelledby="`vue-chart-title-${kind}`"
        data-vue-owned
        data-chart-dialog
        @close="closed"
        @cancel.prevent="close"
      >
        <header class="dialog-header">
          <h2 :id="`vue-chart-title-${kind}`">{{ spec?.title || '' }}</h2>
          <button type="button" class="icon-button" :aria-label="t('common.close')" @click="close">
            ×
          </button>
        </header>
        <div ref="expandedHost" class="chart-dialog-body"></div></dialog
    ></Teleport>
  </div>
</template>
