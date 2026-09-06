<script setup lang="ts">
import { computed } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { formatMessage, messageIds } from '../../core/i18n.ts';
import { createFinancePresentation } from './presentation.ts';
import type { FormView, TaxFormSummary } from './model.ts';
const props = defineProps<{
  code: '130' | '303';
  form: FormView;
  summary?: TaxFormSummary;
  refreshing: boolean;
}>();
const emit = defineEmits<{ refresh: [] }>();
const { locale, t } = useLocale();
const presentation = computed(() => createFinancePresentation(locale.value));
const status = computed(() => props.summary?.settlement_status || 'undetermined');
const money = (value?: number | null) => (value == null ? '—' : eur(value / 100, locale.value));
const keys = computed(() =>
  props.code === '130'
    ? ['01', '02', '03', '04', '05', '07', '19', 'difficult_expenses']
    : [
        '29',
        '45',
        '64',
        '69',
        props.form.values['71'] !== undefined ? '71' : 'result',
        '72',
        '73',
        'compensation_carryforward',
      ],
);
const boxLabel = (key: string) =>
  messageIds.includes(`taxes.boxes.${props.code}.${key}`)
    ? formatMessage(`taxes.boxes.${props.code}.${key}`, {}, locale.value)
    : key;
const filing = computed(() =>
  props.summary?.determination !== 'due'
    ? t('taxes.filingNotRequired')
    : props.summary.filing_status === 'filed'
      ? `${t('taxes.filed')}${props.summary.filed_on ? ` · ${formatDateText(props.summary.filed_on, locale.value)}` : ''}`
      : t('taxes.notFiled'),
);
</script>
<template>
  <section class="panel tax-form-card">
    <header>
      <div>
        <span>{{ t(code === '130' ? 'taxes.irpf' : 'taxes.iva') }}</span>
        <h2>Modelo {{ code }}</h2>
      </div>
      <span class="badge" :class="`status-${presentation.taxStatusTone(status)}`">{{
        presentation.taxSettlementLabel(status)
      }}</span>
    </header>
    <div class="tax-form-amount">
      <span>{{ t('taxes.payable') }}</span
      ><strong>{{ money(summary?.payable_minor) }}</strong>
      <p v-if="summary?.payable_minor === 0">{{ t('taxes.noTaxPayment') }}</p>
      <template v-if="code === '303' && summary?.disposition === 'carryforward'"
        ><p v-if="summary.carryforward_minor != null">
          {{ t('taxes.carryforward') }}: {{ money(summary.carryforward_minor) }}
        </p>
        <p
          v-if="
            summary.generated_credit_minor != null &&
            summary.generated_credit_minor !== summary.carryforward_minor
          "
        >
          {{ t('taxes.generatedCredit') }}: {{ money(summary.generated_credit_minor) }}
        </p></template
      >
      <p
        v-if="
          code === '303' &&
          summary?.disposition === 'refund' &&
          summary.refund_requested_minor != null
        "
      >
        {{ t('taxes.refundRequested') }}: {{ money(summary.refund_requested_minor) }}
      </p>
    </div>
    <dl class="tax-form-facts">
      <div>
        <dt>{{ t('taxes.filing') }}</dt>
        <dd>{{ filing }}</dd>
      </div>
      <div>
        <dt>{{ t('taxes.deadline') }}</dt>
        <dd>{{ formatDateText(summary?.statutory_due_on, locale) }}</dd>
      </div>
      <div v-if="summary?.direct_debit_cutoff_on">
        <dt>{{ t('taxes.directDebit') }}</dt>
        <dd>{{ formatDateText(summary.direct_debit_cutoff_on, locale) }}</dd>
      </div>
    </dl>
    <details class="tax-calculation-details">
      <summary>{{ t('taxes.details') }}</summary>
      <div v-if="Object.keys(form.values).length" class="casilla-grid">
        <template v-for="key in keys" :key="key"
          ><div v-if="form.values[key] !== undefined" class="casilla">
            <span>{{ boxLabel(key) }}</span
            ><strong>{{ eur(form.values[key], locale) }}</strong
            ><small>{{ /^\d+$/.test(key) ? `casilla ${key}` : '' }}</small>
          </div></template
        >
      </div>
      <div v-else class="empty-state">{{ presentation.formEmptyState(form) }}</div>
      <template v-if="form.display_state === 'unavailable'"
        ><p>{{ t('help.words.refreshHint') }}</p>
        <button
          type="button"
          class="secondary-button"
          :disabled="refreshing"
          @click="emit('refresh')"
        >
          {{ t('help.words.refresh') }}
        </button></template
      >
    </details>
  </section>
</template>
