<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { followSpaLink } from '../../vue/services.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { ApiError } from '../../core/http.ts';
import { errorMessage } from '../../core/error-message.ts';
import { formatMessage, localeTag, messageIds } from '../../core/i18n.ts';
import {
  transactionDetail,
  type ExpenseDetailContext,
  type TransactionDetail,
  type TaxTreatment,
} from './model.ts';
const props = defineProps<{ context: ExpenseDetailContext }>();
const { locale, t } = useLocale();
const data = shallowRef<TransactionDetail>();
const error = shallowRef<unknown>();
const followError = shallowRef<unknown>();
const loading = ref(true),
  busy = ref(false);
const back = ref(props.context.returnUrl),
  wrongTypeUrl = ref('/dashboard');
let active = true,
  generation = 0;
let pendingWrite: { transactionId: string } | null = null;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
const current = (revision: number) => active && revision === generation;
async function load() {
  const revision = ++generation;
  const context = props.context;
  loading.value = true;
  error.value = undefined;
  try {
    const result = transactionDetail(
      await context.services.request(
        `/api/transactions/${encodeURIComponent(context.transactionId)}`,
      ),
      context.transactionId,
    );
    if (!current(revision)) return;
    const links = context.resolved(result);
    data.value = result;
    back.value = links.returnUrl;
    wrongTypeUrl.value = links.wrongTypeUrl;
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      loading.value = false;
      context.settled();
    }
  }
}
watch(
  () => props.context,
  () => {
    generation++;
    back.value = props.context.returnUrl;
    wrongTypeUrl.value = '/dashboard';
    if (pendingWrite?.transactionId === props.context.transactionId) return;
    pendingWrite = null;
    busy.value = false;
    data.value = undefined;
    followError.value = undefined;
    void load();
  },
  { immediate: true },
);
async function followUp() {
  if (busy.value) return;
  busy.value = true;
  followError.value = undefined;
  const context = props.context;
  const write = { transactionId: context.transactionId };
  pendingWrite = write;
  const ownsWrite = () =>
    active && pendingWrite === write && props.context.transactionId === write.transactionId;
  try {
    await context.services.request(
      `/api/expense-workflows/${encodeURIComponent(context.transactionId)}/follow-up`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
    );
    // A same-record context update changes presentation/services, not ownership
    // of the server write. Refresh through the latest context when it completes.
    if (ownsWrite()) await load();
  } catch (failure) {
    if (ownsWrite()) followError.value = failure;
  } finally {
    if (ownsWrite()) {
      busy.value = false;
      pendingWrite = null;
    }
  }
}
const tx = computed(() => data.value!.transaction);
const treatments = computed(() => data.value?.tax_treatments || []);
const notes = computed(() =>
  treatments.value.filter((row) => row.notes != null && row.notes !== ''),
);
const minor = (value?: number | null) => (value == null ? '—' : eur(value / 100, locale.value));
const translatedCode = (domain: string, code: string) =>
  messageIds.includes(`${domain}.${code}`)
    ? formatMessage(`${domain}.${code}`, {}, locale.value)
    : domain === 'statuses.labels'
      ? code.replaceAll('_', ' ')
      : code;
function backLabel(url: string) {
  const parsed = new URL(url, window.location.origin);
  if (/^\/contacts\/[0-9a-fA-F-]{32,36}$/.test(parsed.pathname)) return t('contacts.backToParty');
  return t('expense.back', {
    title: translatedCode('titles', parsed.pathname.slice(1) || 'expenses'),
    period: parsed.searchParams.get('period') || '—',
  });
}
const facts = computed(() => {
  const value = tx.value,
    original = value.amount_original_minor ?? value.amount_minor;
  return [
    [t('fields.transactionDate'), formatDateText(value.transaction_date, locale.value)],
    [t('fields.bookingDate'), formatDateText(value.booking_date, locale.value)],
    [t('fields.issuedOn'), formatDateText(data.value?.document?.issued_on, locale.value)],
    [t('toolbar.period'), data.value!.period.period_key],
    [t('expense.description'), value.description || '—'],
    [
      t('fields.amount'),
      original == null
        ? '—'
        : `${new Intl.NumberFormat(localeTag(locale.value), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(original / 100)} ${value.original_currency || value.currency}`,
    ],
    [
      'EUR',
      minor(value.amount_eur_minor ?? (value.currency === 'EUR' ? value.amount_minor : null)),
    ],
  ];
});
const treatmentLabel = (row: TaxTreatment) => `${row.treatment_type} · ${row.jurisdiction}`;
const errorText = (value: unknown) => errorMessage(value, locale.value);
const notFound = computed(() => error.value instanceof ApiError && error.value.status === 404);
const forbidden = computed(
  () => error.value instanceof ApiError && error.value.code === 'session_forbidden',
);
const reload = () => window.location.reload();
const navigate = (event: MouseEvent) => followSpaLink(event, props.context.services.navigate);
</script>
<template>
  <div :aria-busy="loading || busy" class="section-stack expense-detail">
    <a
      v-if="loading || error || data?.transaction.entry_type !== 'expense'"
      class="secondary-button"
      :href="back"
      @click="navigate"
      >{{ backLabel(back) }}</a
    >
    <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="empty-state" role="alert">
      <p>
        {{
          t(
            notFound
              ? 'expense.notFound'
              : forbidden
                ? 'common.sessionExpired'
                : 'common.loadFailed',
          )
        }}
      </p>
      <button
        v-if="!notFound"
        class="secondary-button"
        type="button"
        @click="forbidden ? reload() : load()"
      >
        {{ t(forbidden ? 'common.reload' : 'common.retry') }}
      </button>
      <details v-if="!notFound">
        <summary>{{ t('issues.sourceDetails') }}</summary>
        <p>{{ errorText(error) }}</p>
      </details>
    </div>
    <div v-else-if="data && tx.entry_type !== 'expense'" class="empty-state">
      <p>{{ t('expense.wrongType') }}</p>
      <a class="secondary-button" :href="wrongTypeUrl" @click="navigate">{{
        backLabel(wrongTypeUrl)
      }}</a>
    </div>
    <template v-else-if="data">
      <section v-if="data.workflow_follow_up?.follow_up_pending" class="panel expense-panel-body">
        <p>{{ t('app.inline.postedCalculationRefreshOrInboxCleanupIsPending') }}</p>
        <button id="expense-follow-up" type="button" :disabled="busy" @click="followUp">
          {{ t('app.inline.retryFollowUp') }}
        </button>
        <p id="expense-follow-up-error" role="alert">{{ errorText(followError) }}</p>
      </section>
      <header class="review-workspace-header">
        <a class="secondary-button" :href="back" @click="navigate">{{ backLabel(back) }}</a>
        <div>
          <h2>{{ data.document?.document_number || t('expense.title') }}</h2>
          <p>{{ data.counterparty?.display_name || '—' }}</p>
        </div>
        <div>
          <span class="badge" :class="tx.lifecycle_status.replace(/[^a-z0-9_-]/gi, '_')">{{
            translatedCode('statuses.labels', tx.lifecycle_status)
          }}</span>
          <p>{{ t('expense.readOnly') }}</p>
        </div>
      </header>
      <section class="panel expense-notes" aria-labelledby="expense-notes-title">
        <header class="panel-header">
          <h2 id="expense-notes-title">{{ t('expense.notes') }}</h2>
        </header>
        <div class="expense-panel-body">
          <template v-if="notes.length"
            ><div v-for="(row, index) in notes" :key="index" class="expense-note">
              <small v-if="treatments.length > 1">{{ treatmentLabel(row) }}</small>
              <p class="expense-note-text">{{ row.notes }}</p>
            </div></template
          >
          <p v-else>{{ t('expense.noNotes') }}</p>
        </div>
      </section>
      <section class="panel">
        <header class="panel-header">
          <h2>{{ t('review.factsTitle') }}</h2>
          <small>{{ data.period.period_key }}</small>
        </header>
        <div class="expense-panel-body">
          <dl class="review-facts-list">
            <div v-for="[label, value] in facts" :key="label">
              <dt>{{ label }}</dt>
              <dd>{{ value }}</dd>
            </div>
          </dl>
          <a
            v-if="data.document?.document_id"
            class="text-button"
            :href="`/api/document/${encodeURIComponent(data.document.document_id)}/content`"
            target="_blank"
            rel="noreferrer"
            >{{ t('common.file') }}</a
          >
        </div>
      </section>
      <section class="panel">
        <header class="panel-header">
          <h2>{{ t('review.taxDecision') }}</h2>
        </header>
        <div class="expense-panel-body">
          <template v-if="treatments.length"
            ><div v-for="(row, index) in treatments" :key="index" class="expense-treatment">
              <small>{{ treatmentLabel(row) }}</small>
              <dl class="review-facts-list">
                <div>
                  <dt>{{ t('fields.taxCode') }}</dt>
                  <dd>{{ translatedCode('taxCodeLabels', row.tax_code || '') }}</dd>
                </div>
                <div>
                  <dt>{{ t('transactions.irpfDeduction') }}</dt>
                  <dd>{{ minor(row.deductible_irpf_minor) }}</dd>
                </div>
                <div>
                  <dt>IVA</dt>
                  <dd>{{ minor(row.deductible_vat_minor) }}</dd>
                </div>
                <div>
                  <dt>{{ t('review.vatInvestment') }}</dt>
                  <dd>
                    {{
                      t(
                        row.vat_investment_good == null
                          ? 'review.vatUnknown'
                          : row.vat_investment_good
                            ? 'review.vatAsset'
                            : 'review.vatCurrent',
                      )
                    }}
                  </dd>
                </div>
                <div v-for="form in [130, 303, 347] as const" :key="form">
                  <dt>Modelo {{ form }}</dt>
                  <dd>
                    {{
                      row[`include_modelo${form}`] == null
                        ? '—'
                        : t(row[`include_modelo${form}`] ? 'common.yes' : 'common.no')
                    }}
                  </dd>
                </div>
              </dl>
            </div></template
          >
          <p v-else>{{ t('common.noRecords') }}</p>
        </div>
      </section>
    </template>
  </div>
</template>
