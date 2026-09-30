<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { ApiError } from '../../core/http.ts';
import { errorMessage } from '../../core/error-message.ts';
import { followSpaLink } from '../../vue/services.ts';
import { transactionDetail } from '../expense-detail/model.ts';
import ExpenseWorkflowView from './ExpenseWorkflowView.vue';
import GuidedReviewView from './GuidedReviewView.vue';
import type { ReviewDetailContext } from './guided-model.ts';
const props = defineProps<{ context: ReviewDetailContext }>();
const { locale, t } = useLocale();
const mode = ref<'loading' | 'workflow' | 'guided'>('loading'),
  error = shallowRef<unknown>(),
  factsOnly = ref(false),
  back = ref(props.context.returnUrl),
  period = ref(props.context.period);
let active = true,
  generation = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
async function load() {
  const revision = ++generation,
    context = props.context;
  mode.value = 'loading';
  error.value = undefined;
  factsOnly.value = false;
  try {
    const data = transactionDetail(
      await context.services.request(
        `/api/transactions/${encodeURIComponent(context.transactionId)}`,
      ),
      context.transactionId,
    );
    if (!active || revision !== generation) return;
    period.value = data.period.period_key;
    back.value = context.resolved(data.period);
    if (
      data.transaction.entry_type === 'expense' &&
      ['posted', 'included_in_snapshot'].includes(data.transaction.lifecycle_status)
    ) {
      context.posted(context.transactionId, period.value, true);
      return;
    }
    mode.value = data.transaction.entry_type === 'expense' ? 'workflow' : 'guided';
  } catch (failure) {
    if (active && revision === generation) error.value = failure;
  } finally {
    if (active && revision === generation && error.value) props.context.settled();
  }
}
watch(
  () => props.context,
  (context, previous) => {
    if (
      previous?.transactionId === context.transactionId &&
      mode.value !== 'loading' &&
      !error.value
    ) {
      back.value = context.returnUrl;
      return;
    }
    void load();
  },
  { immediate: true },
);
const guidedContext = computed(() => ({
  ...props.context,
  returnUrl: back.value,
  factsOnly: factsOnly.value,
}));
const workflowContext = computed(() => ({
  transactionId: props.context.transactionId,
  services: props.context.services,
  settled: props.context.settled,
  onPosted: (result: { transaction_id: string }) => {
    if (active) props.context.posted(result.transaction_id, period.value, false);
  },
  onLegacy: () => {
    if (active) {
      factsOnly.value = true;
      mode.value = 'guided';
    }
  },
}));
const forbidden = computed(
  () => error.value instanceof ApiError && error.value.code === 'session_forbidden',
);
const retry = () => (forbidden.value ? window.location.reload() : void load());
</script>
<template>
  <div v-if="error" class="empty-state state-error" role="alert">
    <a
      class="secondary-button"
      :href="back"
      @click="followSpaLink($event, context.services.navigate)"
      >{{ t('review.workspaceBack') }}</a
    >
    <p>{{ t(forbidden ? 'common.sessionExpired' : 'common.loadFailed') }}</p>
    <details>
      <summary>{{ t('issues.sourceDetails') }}</summary>
      <p>{{ errorMessage(error, locale) }}</p>
    </details>
    <button class="secondary-button" @click="retry">
      {{ t(forbidden ? 'common.reload' : 'common.retry') }}
    </button>
  </div>
  <div v-else-if="mode === 'loading'" class="empty-state" role="status">
    {{ t('common.loading') }}
  </div>
  <ExpenseWorkflowView
    v-else-if="mode === 'workflow'"
    :key="context.transactionId"
    :context="workflowContext"
  />
  <GuidedReviewView v-else :key="context.transactionId" :context="guidedContext" />
</template>
