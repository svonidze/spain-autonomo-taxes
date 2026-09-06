<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import ChartHost from '../../charts/ChartHost.vue';
import type { Analytics } from '../../charts/types.ts';
import type { ChartKind } from '../../charts/builders.ts';
import StatusCell from '../../components/StatusCell.vue';
import RecentTransactions from './RecentTransactions.vue';
import { createFinancePresentation } from './presentation.ts';
import type { OverviewContext, DashboardData } from './model.ts';
const props = defineProps<{ context: OverviewContext }>();
const { locale, t } = useLocale();
const data = shallowRef<DashboardData>(),
  analytics = shallowRef<Analytics>(),
  error = shallowRef<unknown>();
const chartFailure = ref(false),
  loading = ref(true),
  busy = ref(false);
let active = true,
  generation = 0;
const current = (revision: number) => active && revision === generation;
async function load(refresh = false) {
  const revision = ++generation,
    context = props.context;
  busy.value = true;
  loading.value = !data.value;
  error.value = undefined;
  try {
    const [result, chart] = await Promise.all([
      context.services.request(
        `/api/dashboard?period=${encodeURIComponent(context.period)}`,
      ) as Promise<DashboardData>,
      context.services
        .request(`/api/analytics?period=${encodeURIComponent(context.period)}`)
        .then((value) => ({ ok: true as const, value: value as Analytics }))
        .catch(() => ({ ok: false as const })),
    ]);
    if (!current(revision) || (refresh && document.querySelector('dialog[open]'))) return;
    data.value = result;
    analytics.value = chart.ok ? chart.value : undefined;
    chartFailure.value = !chart.ok;
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      busy.value = false;
      loading.value = false;
      context.settled();
    }
  }
}
watch(
  () => props.context,
  () => {
    data.value = undefined;
    analytics.value = undefined;
    void load();
  },
  { immediate: true },
);
function refreshVisible() {
  if (
    !busy.value &&
    document.visibilityState === 'visible' &&
    !document.querySelector('dialog[open]')
  )
    void load(true);
}
const interval = setInterval(refreshVisible, 60000);
document.addEventListener('visibilitychange', refreshVisible);
onBeforeUnmount(() => {
  active = false;
  generation++;
  clearInterval(interval);
  document.removeEventListener('visibilitychange', refreshVisible);
});
const presentation = computed(() => createFinancePresentation(locale.value));
const integer = (value: unknown, fallback = 0) => {
  const number = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(number) ? number : fallback;
};
const approved = computed(() => {
  const summary = data.value?.posting_preview_summary || data.value?.summary;
  return integer(
    summary?.approved_count,
    integer(summary?.ready_count, integer(summary?.ready_to_post)) +
      integer(summary?.deferred_count) +
      integer(summary?.blocked_count),
  );
});
const ready = computed(() =>
  Number(
    data.value?.readyCount ??
      data.value?.ready_count ??
      data.value?.totals.forecast.transaction_count ??
      0,
  ),
);
const nextDue = computed(() =>
  data.value?.obligations.find(
    (row) => row.determination === 'due' && row.filing_status !== 'filed',
  ),
);
const form = (code: string) =>
  presentation.value.formCardData(
    data.value?.tax_forms?.[`modelo${code}`],
    data.value?.obligations.find((row) => String(row.obligation_code) === code),
    { warnOnMissingHeadline: true },
  );
const charts: { id: string; kind: ChartKind }[] = [
  { id: 'chart-business-result', kind: 'businessResult' },
  { id: 'chart-tax-due', kind: 'taxDue' },
  { id: 'chart-iva-position', kind: 'ivaPosition' },
  { id: 'chart-tax-reserve', kind: 'reserve' },
  { id: 'chart-cumulative-net', kind: 'cumulativeNet' },
];
const review = (posting = false) =>
  props.context.services.navigate(
    `/review?${new URLSearchParams({ period: props.context.period, ...(posting ? { tab: 'posting' } : {}) })}`,
  );
const forbidden = () => error.value instanceof ApiError && error.value.code === 'session_forbidden';
const retry = () => (forbidden() ? window.location.reload() : void load(!!data.value));
</script>
<template>
  <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
  <div v-else>
    <div
      v-if="error"
      id="dashboard-expense-refresh-status"
      class="expense-refresh-status"
      role="alert"
    >
      <div id="dashboard-expense-refresh-message">
        <p>
          {{
            t(
              forbidden()
                ? 'common.sessionExpired'
                : data
                  ? 'expense.refreshFailed'
                  : 'common.loadFailed',
            )
          }}
        </p>
        <p>{{ errorMessage(error, locale) }}</p>
      </div>
      <button
        id="dashboard-expense-refresh-retry"
        class="secondary-button"
        :disabled="busy"
        @click="retry"
      >
        {{ t(forbidden() ? 'common.reload' : 'expense.refreshData') }}
      </button>
    </div>
    <template v-if="data">
      <div v-if="approved" class="period-note posting-banner">
        <div>
          <strong>{{ t('dashboard.postingBannerTitle', { count: approved }) }}</strong>
          <p>{{ t('dashboard.approvedNotPosted') }}</p>
        </div>
        <button class="secondary-button" type="button" @click="review(true)">
          {{ t('dashboard.postingBannerCta') }}
        </button>
      </div>
      <div class="metric-grid">
        <div class="metric accent">
          <span>{{ t('dashboard.incomePosted') }}</span
          ><strong>{{ eur(data.totals.actual.income_eur, locale) }}</strong
          ><small>{{
            t('common.counts.operations', { count: data.totals.actual.income_transaction_count })
          }}</small>
        </div>
        <div v-for="kind in ['purchase', 'amortization'] as const" :key="kind" class="metric">
          <span>{{ t(kind === 'purchase' ? 'expense.purchase' : 'expense.amortization') }}</span
          ><strong>{{ eur(data.expense_summary[kind]?.posted?.amount_eur, locale) }}</strong
          ><small>{{ t('expense.posted') }}</small
          ><small>{{
            t('expense.approved', {
              amount: eur(data.expense_summary[kind]?.approved?.amount_eur, locale),
            })
          }}</small
          ><small v-if="data.expense_summary[kind]?.future_approved?.count">{{
            t('expense.futureAmount', {
              amount: eur(data.expense_summary[kind].future_approved.amount_eur, locale),
            })
          }}</small
          ><small
            v-if="
              data.expense_summary[kind] &&
              [data.expense_summary[kind].posted, data.expense_summary[kind].approved].some(
                (scope) => scope.missing_amount_count,
              )
            "
            >{{ t('expense.missing') }}</small
          >
        </div>
        <div
          v-for="code in ['130', '303']"
          :key="code"
          class="metric"
          :class="presentation.formAccentClass(form(code))"
        >
          <span>{{
            t(code === '130' ? 'dashboard.modelo130Box' : 'dashboard.modelo303Result')
          }}</span
          ><strong>{{ presentation.formCardValue(form(code)) }}</strong
          ><small>{{
            presentation.formSubtitle(
              form(code),
              data.obligations.find((row) => String(row.obligation_code) === code),
            )
          }}</small>
        </div>
      </div>
      <button
        v-if="ready > 0"
        class="review-banner"
        type="button"
        id="dashboard-ready-banner"
        @click="review()"
      >
        <strong>{{
          t(ready === 1 ? 'dashboard.readyBannerOne' : 'dashboard.readyBannerOther', {
            count: ready,
          })
        }}</strong
        ><span>{{ t('dashboard.readyBannerAction') }}</span>
      </button>
      <div v-if="nextDue" class="deadline-strip">
        <strong>Modelo {{ nextDue.obligation_code }}</strong>
        <p>{{ t('dashboard.obligationDue') }}</p>
        <time :datetime="nextDue.statutory_due_on || ''">{{
          formatDateText(nextDue.statutory_due_on, locale)
        }}</time>
      </div>
      <section class="charts-panel" id="dashboard-charts">
        <ChartHost
          v-for="chart in charts"
          :key="chart.kind"
          :id="chart.id"
          :kind="chart.kind"
          :period="context.period"
          :analytics="analytics"
          :failed="chartFailure"
        />
      </section>
      <div class="dashboard-grid">
        <section class="panel">
          <header class="panel-header">
            <h2>{{ t('dashboard.recentTransactions') }}</h2>
            <small>{{ context.period }}</small>
          </header>
          <RecentTransactions
            :rows="data.recent_transactions"
            :period="context.period"
            :target="context.copyTarget"
            :navigate="context.services.navigate"
            :copy="context.copy"
          />
        </section>
        <section class="panel">
          <header class="panel-header">
            <h2>{{ t('dashboard.needsAttention') }}</h2>
            <small>{{ data.open_issues.length }}</small>
          </header>
          <ul v-if="data.open_issues.length" class="issues-list">
            <li
              v-for="(issue, index) in data.open_issues"
              :key="issue.ui_context.subject_id || index"
            >
              <StatusCell :context="issue.ui_context" />
            </li>
          </ul>
          <div v-else class="empty-state">{{ t('issues.none') }}</div>
        </section>
      </div>
    </template>
  </div>
</template>
