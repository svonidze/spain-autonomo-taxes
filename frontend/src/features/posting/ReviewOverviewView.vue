<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateTime } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import { message } from '../../core/i18n.ts';
import ReviewQueue from './ReviewQueue.vue';
import ReviewDocuments from './ReviewDocuments.vue';
import PostingRows from './PostingRows.vue';
import {
  preview as normalizePreview,
  result as normalizeResult,
  postItems,
  statusLabel,
} from './normalize.ts';
import { postingState, markStale, refreshToken, clearRefresh } from './state.ts';
import type {
  OverviewData,
  PostingItem,
  PostingPreview,
  ReviewOverviewContext,
  ReviewTab,
} from './model.ts';
const props = defineProps<{ context: ReviewOverviewContext }>();
const { locale, t } = useLocale();
const data = shallowRef<OverviewData>(),
  error = shallowRef<unknown>(),
  loading = ref(true),
  refreshing = ref(false),
  dialogError = shallowRef<unknown>();
const dialog = ref<HTMLDialogElement>(),
  tablist = ref<HTMLElement>();
const frozen = shallowRef<{
  period: string;
  rows: PostingItem[];
  summary: PostingPreview['summary'];
}>();
const submitting = postingState.busy;
let active = true,
  generation = 0,
  opener: HTMLElement | undefined;
const current = (version: number, period = props.context.period) =>
  active && version === generation && props.context.period === period;
onBeforeUnmount(() => {
  active = false;
  generation++;
  dialog.value?.close();
});
async function load() {
  const version = ++generation,
    context = props.context;
  loading.value = true;
  error.value = undefined;
  try {
    const query = `?period=${encodeURIComponent(context.period)}`;
    const [rows, issues, documents, payload] = await Promise.all([
      context.services.request('/api/transactions' + query + '&status=review'),
      context.services.request('/api/issues' + query),
      context.services.request('/api/documents' + query),
      context.services.request('/api/review/posting-preview' + query),
    ]);
    if (!current(version, context.period)) return;
    const preview = normalizePreview(payload, context.period);
    if (preview.period !== context.period) throw new Error('review.postingPreviewMismatch');
    data.value = {
      rows: rows as OverviewData['rows'],
      issues: issues as OverviewData['issues'],
      documents: documents as OverviewData['documents'],
      preview,
    };
  } catch (failure) {
    if (current(version, context.period)) error.value = failure;
  } finally {
    if (current(version, context.period)) {
      loading.value = false;
      props.context.settled();
    }
  }
}
watch(
  () => props.context,
  (context, previous) => {
    if (
      previous?.period === context.period &&
      data.value &&
      (submitting.value || dialog.value?.open)
    )
      return;
    if (previous?.period !== context.period) {
      data.value = undefined;
      close();
    }
    void load();
  },
  { immediate: true },
);
const tabs: ReviewTab[] = ['queue', 'posting', 'documents'];
const selected = computed(() => (tabs.includes(props.context.tab) ? props.context.tab : 'queue'));
const counts = computed(() => ({
  queue: data.value?.rows.filter((row) => row.ui_context?.state === 'needs_review').length || 0,
  posting: data.value?.preview.summary.readyCount || 0,
  documents: data.value?.issues.length || 0,
}));
const canPost = computed(
  () =>
    !!data.value &&
    !loading.value &&
    !error.value &&
    !submitting.value &&
    data.value.preview.periodStatus === 'open' &&
    data.value.preview.ready.length > 0,
);
const stale = computed(() => postingState.stale.get(props.context.period));
const last = computed(() =>
  postingState.lastResult.value?.period === props.context.period
    ? postingState.lastResult.value.result
    : undefined,
);
const stats = computed(() => {
  const p = data.value!.preview;
  return [
    { label: t('review.postingApproved'), count: p.summary.approvedCount },
    {
      label: t('review.postingBatchReady'),
      count: p.summary.readyCount,
      detail: eur(p.summary.readyTotalEur, locale.value),
    },
    { label: t('review.postingDeferred'), count: p.summary.deferredCount },
    { label: t('review.postingBatchBlocked'), count: p.summary.blockedCount },
    { label: t('review.postingCleanup'), count: p.summary.cleanupCount },
    { label: t('review.postingCleanupBlocked'), count: p.summary.cleanupBlockedCount },
  ];
});
const stamp = computed(() => {
  const value = data.value?.preview.generatedAt || data.value?.preview.asOf;
  return value
    ? t('review.postingGeneratedAt', { date: formatDateTime(value, locale.value) })
    : t('review.postingGeneratedUnknown');
});
const hint = computed(() =>
  t(
    data.value?.preview.periodStatus !== 'open'
      ? 'review.postingClosedHint'
      : !data.value.preview.ready.length
        ? 'review.postingNothingReady'
        : 'review.postingReadyHint',
  ),
);
function tabKey(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
  const buttons = [
      ...(tablist.value?.querySelectorAll<HTMLButtonElement>('[data-review-tab]') || []),
    ],
    index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  if (index < 0) return;
  event.preventDefault();
  buttons[(index + (event.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length].focus();
}
async function open(event: MouseEvent) {
  if (!canPost.value) return;
  opener = event.currentTarget as HTMLElement;
  const p = data.value!.preview;
  frozen.value = JSON.parse(
    JSON.stringify({ period: p.period || props.context.period, rows: p.ready, summary: p.summary }),
  );
  dialogError.value = undefined;
  await nextTick();
  if (active && !dialog.value?.open) dialog.value?.showModal();
}
function close() {
  dialog.value?.close();
  dialogError.value = undefined;
  frozen.value = undefined;
}
function closed() {
  if (opener?.isConnected) opener.focus({ preventScroll: true });
  opener = undefined;
}
async function retryRefresh() {
  if (refreshing.value) return;
  refreshing.value = true;
  const period = props.context.period,
    version = generation,
    token = refreshToken(period);
  try {
    await props.context.refreshCalculation(period);
    clearRefresh(period, token);
    if (current(version, period)) await load();
  } catch (failure) {
    if (postingState.stale.get(period) === token)
      markStale(period, errorMessage(failure, locale.value));
    if (current(version, period)) props.context.notify(errorMessage(failure, locale.value), true);
  } finally {
    if (active) refreshing.value = false;
  }
}
async function submit() {
  if (
    submitting.value ||
    !frozen.value?.rows.length ||
    !data.value ||
    data.value.preview.periodStatus !== 'open'
  )
    return;
  const snapshot = frozen.value,
    version = generation,
    context = props.context;
  submitting.value = true;
  dialogError.value = undefined;
  try {
    const payload = await context.services.request('/api/review/post-ready', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ period: snapshot.period, items: postItems(snapshot.rows) }),
    });
    const result = normalizeResult(payload, snapshot.rows);
    postingState.lastResult.value = { period: snapshot.period, result };
    if (!current(version, snapshot.period)) {
      if (result.summary.postedCount > 0) markStale(snapshot.period);
      return;
    }
    close();
    if (result.summary.postedCount > 0) {
      const token = markStale(snapshot.period);
      try {
        await props.context.refreshCalculation(snapshot.period);
        clearRefresh(snapshot.period, token);
      } catch (failure) {
        if (postingState.stale.get(snapshot.period) === token)
          markStale(snapshot.period, errorMessage(failure, locale.value));
      }
    }
    if (!current(version, snapshot.period)) return;
    if (result.status === 'interrupted') props.context.notify(message('review.postInterrupted'));
    else if (result.status === 'partial') props.context.notify(message('review.postPartial'));
    else if (result.summary.postedCount > 0)
      props.context.notify(message('review.postSuccess', { count: result.summary.postedCount }));
    else props.context.notify(message('review.postNone'));
    await load();
  } catch (failure) {
    if (current(version, snapshot.period)) {
      dialogError.value = failure;
      props.context.notify(errorMessage(failure, locale.value), true);
    }
  } finally {
    submitting.value = false;
  }
}
const forbidden = () => error.value instanceof ApiError && error.value.code === 'session_forbidden';
const reload = () => (forbidden() ? window.location.reload() : void load());
</script>
<template>
  <div class="section-stack review-shell">
    <div v-if="loading && !data" class="empty-state" role="status">{{ t('common.loading') }}</div>
    <div v-if="error" class="empty-state state-error" role="alert">
      <p>{{ t(forbidden() ? 'common.sessionExpired' : 'common.loadFailed') }}</p>
      <button class="secondary-button" @click="reload">
        {{ t(forbidden() ? 'common.reload' : 'common.retry') }}
      </button>
      <details>
        <summary>{{ t('issues.sourceDetails') }}</summary>
        <p>{{ errorMessage(error, locale) }}</p>
      </details>
    </div>
    <template v-if="data">
      <div
        ref="tablist"
        class="review-tabs"
        role="tablist"
        :aria-label="t('reviewTabs.aria')"
        @keydown="tabKey"
      >
        <button
          v-for="tab in tabs"
          :key="tab"
          type="button"
          role="tab"
          :id="`review-tab-${tab}`"
          :aria-selected="selected === tab"
          aria-controls="review-tabpanel"
          :tabindex="selected === tab ? 0 : -1"
          :data-review-tab="tab"
          @click="context.selectTab(tab)"
        >
          {{
            t(
              tab === 'queue'
                ? 'reviewTabs.queue'
                : tab === 'posting'
                  ? 'reviewTabs.posting'
                  : 'reviewTabs.documents',
            )
          }}<span v-if="counts[tab]" class="tab-count">{{ counts[tab] }}</span>
        </button>
      </div>
      <div
        class="section-stack"
        id="review-tabpanel"
        role="tabpanel"
        :aria-labelledby="`review-tab-${selected}`"
      >
        <ReviewQueue v-if="selected === 'queue'" :rows="data.rows" :context="context" />
        <ReviewDocuments
          v-else-if="selected === 'documents'"
          :documents="data.documents"
          :issues="data.issues"
        />
        <section v-else class="panel">
          <header class="panel-header">
            <h2>{{ t('review.postingTitle') }}</h2>
            <small>{{ context.period }}</small>
          </header>
          <div class="posting-panel">
            <div v-if="stale" class="period-note posting-warning">
              <div>
                <strong>{{ t('review.postingStaleWarning') }}</strong>
                <p v-if="stale.error">{{ stale.error }}</p>
              </div>
              <button
                type="button"
                class="secondary-button"
                data-posting-action="retry-refresh"
                :disabled="refreshing"
                @click="retryRefresh"
              >
                {{ t('review.postingRetryRefresh') }}
              </button>
            </div>
            <div v-if="data.preview.periodStatus !== 'open'" class="period-note posting-warning">
              <div>
                <strong>{{ t('review.postingClosedPeriod') }}</strong>
                <p>{{ t('review.postingClosedHint') }}</p>
              </div>
            </div>
            <div class="posting-summary-grid">
              <div v-for="stat in stats" :key="stat.label" class="posting-stat">
                <span>{{ stat.label }}</span
                ><strong>{{ stat.count }}</strong
                ><small>{{ stat.detail || ' ' }}</small>
              </div>
            </div>
            <div class="posting-action-row">
              <div class="posting-meta">
                <strong>{{
                  t(
                    data.preview.periodStatus === 'open'
                      ? 'review.postingOpenPeriod'
                      : 'review.postingClosedPeriod',
                  )
                }}</strong
                ><span>{{ stamp }}</span>
              </div>
              <button
                type="button"
                class="primary-button"
                data-posting-action="open-confirm"
                :disabled="!canPost"
                @click="open"
              >
                {{ t('review.postConfirmAction') }}
              </button>
            </div>
            <p class="posting-action-hint">{{ hint }}</p>
            <PostingRows
              :title="t('review.postingDeferred')"
              :rows="data.preview.deferred"
            /><PostingRows :title="t('review.postingBatchBlocked')" :rows="data.preview.blocked" />
            <div v-if="last" class="posting-results">
              <div class="posting-results-header">
                <h3>{{ t('review.postingResults') }}</h3>
                <p>
                  {{
                    t('review.postingResultSummary', {
                      status: statusLabel(last.status),
                      count: last.summary.postedCount,
                    })
                  }}
                </p>
              </div>
              <PostingRows :title="t('review.postingRows')" :rows="last.results" />
            </div>
          </div>
        </section>
      </div>
    </template>
    <Teleport to="body"
      ><dialog
        ref="dialog"
        id="vue-posting-confirm-dialog"
        class="intake-dialog posting-dialog"
        aria-labelledby="vue-posting-title"
        data-vue-owned
        @cancel.prevent="close"
        @close="closed"
      >
        <form method="dialog" @submit.prevent="submit">
          <header class="dialog-header">
            <div>
              <h2 id="vue-posting-title">{{ t('review.postConfirmTitle') }}</h2>
              <p>{{ frozen?.period }}</p>
            </div>
            <button
              type="button"
              class="icon-button"
              :aria-label="t('common.close')"
              @click="close"
            >
              ×
            </button>
          </header>
          <div class="dialog-body">
            <p>{{ t('review.postConfirmLead') }}</p>
            <div
              v-if="
                frozen &&
                (frozen.summary.cleanupCount > 0 || frozen.summary.cleanupBlockedCount > 0)
              "
              class="period-note posting-warning"
            >
              <div>
                <strong>{{
                  t('review.postConfirmCleanupWarning', {
                    cleanupCount: frozen.summary.cleanupCount,
                    cleanupBlockedCount: frozen.summary.cleanupBlockedCount,
                  })
                }}</strong>
              </div>
            </div>
            <PostingRows :title="t('review.postingRows')" :rows="frozen?.rows || []" />
          </div>
          <footer class="dialog-actions">
            <span role="status">{{ errorMessage(dialogError, locale) }}</span
            ><button type="button" class="secondary-button" @click="close">
              {{ t('common.cancel') }}</button
            ><button
              id="vue-confirm-posting"
              type="submit"
              class="primary-button"
              :disabled="submitting || !frozen?.rows.length"
            >
              {{ t('review.postConfirmAction') }}
            </button>
          </footer>
        </form>
      </dialog></Teleport
    >
  </div>
</template>
