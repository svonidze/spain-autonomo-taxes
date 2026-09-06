<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import { formatMessage, message, messageIds } from '../../core/i18n.ts';
import ChartHost from '../../charts/ChartHost.vue';
import TaxFormCard from './TaxFormCard.vue';
import { createFinancePresentation } from './presentation.ts';
import type { OverviewContext, TaxesData } from './model.ts';
const props = defineProps<{ context: OverviewContext }>();
const { locale, t } = useLocale();
const data = shallowRef<TaxesData>(),
  error = shallowRef<unknown>(),
  loading = ref(true),
  refreshing = ref(false);
let active = true,
  generation = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
async function load() {
  const revision = ++generation,
    context = props.context;
  loading.value = !data.value;
  error.value = undefined;
  try {
    const result = (await context.services.request(
      `/api/taxes?period=${encodeURIComponent(context.period)}`,
    )) as TaxesData;
    if (active && revision === generation) data.value = result;
  } catch (failure) {
    if (active && revision === generation) error.value = failure;
  } finally {
    if (active && revision === generation) {
      loading.value = false;
      context.settled();
    }
  }
}
watch(
  () => props.context,
  () => {
    data.value = undefined;
    void load();
  },
  { immediate: true },
);
async function refresh() {
  if (refreshing.value) return;
  const revision = generation,
    context = props.context;
  refreshing.value = true;
  try {
    await context.refreshCalculation();
    if (active && revision === generation) {
      context.notify(message('refresh.done', { period: context.period }));
      await load();
    }
  } catch (failure) {
    if (active && revision === generation)
      context.notify(errorMessage(failure, locale.value), true);
  } finally {
    if (active) refreshing.value = false;
  }
}
const p = computed(() => createFinancePresentation(locale.value));
const headline = computed(() =>
  p.value.taxHeadlineModel(data.value?.period_state, data.value?.tax_summary),
);
const extraDue = computed(
  () =>
    data.value?.obligations.filter(
      (row) => row.determination === 'due' && !['130', '303'].includes(String(row.obligation_code)),
    ) || [],
);
const other = computed(
  () => data.value?.obligations.filter((row) => row.determination !== 'due') || [],
);
const money = (value?: number | null) => (value == null ? '—' : eur(value / 100, locale.value));
const statusLabel = (value: string) =>
  messageIds.includes(`statuses.labels.${value}`)
    ? formatMessage(`statuses.labels.${value}`, {}, locale.value)
    : value.replaceAll('_', ' ');
const form = (code: string) =>
  p.value.formCardData(
    data.value?.tax_forms?.[`modelo${code}`],
    data.value?.obligations.find((row) => String(row.obligation_code) === code),
  );
const forbidden = () => error.value instanceof ApiError && error.value.code === 'session_forbidden';
const retry = () => (forbidden() ? window.location.reload() : void load());
</script>
<template>
  <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
  <div v-else-if="error" class="empty-state state-error" role="alert">
    <p>{{ t(forbidden() ? 'common.sessionExpired' : 'common.loadFailed') }}</p>
    <button class="secondary-button" @click="retry">
      {{ t(forbidden() ? 'common.reload' : 'common.retry') }}
    </button>
    <details>
      <summary>{{ t('issues.sourceDetails') }}</summary>
      <p>{{ errorMessage(error, locale) }}</p>
    </details>
  </div>
  <div v-else-if="data" class="tax-page">
    <section class="tax-hero" :class="`tax-hero-${headline.tone}`">
      <div class="tax-hero-copy">
        <span>{{ data.period }}</span>
        <h2>{{ headline.label }}</h2>
        <strong v-if="headline.amount != null">{{ money(headline.amount) }}</strong>
        <p>{{ headline.detail }}</p>
      </div>
      <div
        v-if="data.tax_summary && data.period_state?.phase !== 'future'"
        class="tax-summary-facts"
      >
        <div>
          <span>{{ t('taxes.payable') }}</span
          ><strong>{{ money(data.tax_summary.total_payable_minor) }}</strong>
        </div>
        <div>
          <span>{{ t('taxes.confirmedDebit') }}</span
          ><strong>{{ money(Number(data.tax_summary.total_confirmed_paid_minor || 0)) }}</strong>
        </div>
        <div>
          <span>{{
            t(
              Number(data.tax_summary.overpaid_minor || 0) > 0
                ? 'taxes.overpayment'
                : 'taxes.remaining',
            )
          }}</span
          ><strong>{{
            money(
              Number(data.tax_summary.overpaid_minor || 0) > 0
                ? data.tax_summary.overpaid_minor
                : data.tax_summary.outstanding_minor,
            )
          }}</strong>
        </div>
      </div>
    </section>
    <p v-if="data.tax_summary?.requires_reconciliation" class="period-note posting-warning">
      {{ t('taxes.amendedNotice') }}
    </p>
    <div class="tax-form-grid">
      <TaxFormCard
        v-for="code in ['130', '303'] as const"
        :key="code"
        :code="code"
        :form="form(code)"
        :summary="data.tax_summary?.forms?.[code]"
        :refreshing="refreshing"
        @refresh="refresh"
      />
    </div>
    <template
      v-for="group in [
        { rows: extraDue, title: t('taxes.additionalDueForms'), open: true },
        { rows: other, title: t('taxes.otherForms'), open: false },
      ]"
      :key="group.open ? 'due' : 'other'"
      ><details v-if="group.rows.length" class="panel tax-other-forms" :open="group.open">
        <summary>{{ group.title }}</summary>
        <ul>
          <li v-for="row in group.rows" :key="row.obligation_code">
            <strong>Modelo {{ row.obligation_code }}</strong
            ><span>{{
              row.determination === 'due'
                ? p.taxSettlementLabel('undetermined')
                : statusLabel(row.determination)
            }}</span
            ><time :datetime="row.statutory_due_on || ''">{{
              formatDateText(row.statutory_due_on, locale)
            }}</time>
          </li>
        </ul>
      </details></template
    >
    <section class="panel tax-analytics-panel">
      <header class="panel-header">
        <h2>{{ t('taxes.additionalAnalytics') }}</h2>
      </header>
      <ChartHost
        id="chart-ytd-comparison"
        kind="yearComparison"
        :period="context.period"
        :request="context.services.request"
      />
    </section>
  </div>
</template>
