<script setup lang="ts">
import { computed } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { quarterNumber } from '../../core/i18n.ts';
import { followSpaLink } from '../../vue/services.ts';
import StatusCell from '../../components/StatusCell.vue';
import DocumentLabel from './DocumentLabel.vue';
import { hasAmount, type TransactionRow, type ExpenseKind } from './model.ts';
const props = defineProps<{
  rows: TransactionRow[];
  kind: ExpenseKind;
  period: string;
  returnUrl: string;
  navigate: (url: string) => void;
}>();
const { locale, t } = useLocale();
const amortization = computed(() => props.kind === 'amortization');
const headers = computed(() =>
  amortization.value
    ? [
        t('expense.assetDocument'),
        t('expense.periodDate'),
        t('transactions.status'),
        t('expense.quarterAmount'),
      ]
    : [
        t('expense.recognitionDate'),
        t('transactions.counterpartyDocument'),
        t('transactions.status'),
        t('expense.amount'),
        t('transactions.irpfDeduction'),
        'IVA',
      ],
);
const date = (value?: string) => formatDateText(value, locale.value);
const money = (value: unknown) => eur(value, locale.value);
const quarter = (period: string) => {
  const match = /^(\d{4})-Q([1-4])$/.exec(period);
  return match
    ? t('expense.quarter', {
        year: match[1],
        quarter: quarterNumber(Number(match[2]), locale.value),
      })
    : period || '—';
};
const matched = (row: TransactionRow) => row.asset_match_count === 1 && row.asset_id;
</script>
<template>
  <div class="table-wrap">
    <table class="expense-table" role="table">
      <thead role="rowgroup">
        <tr role="row">
          <th v-for="label in headers" :key="label" scope="col" role="columnheader">{{ label }}</th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        <tr
          v-for="row in rows"
          :key="row.transaction_id"
          role="row"
          :data-transaction-id="row.transaction_id"
        >
          <td v-if="!amortization" role="cell" :data-label="headers[0]">
            <div>
              {{ date(row.transaction_date)
              }}<small
                v-if="row.document_issued_on && row.document_issued_on !== row.transaction_date"
                class="expense-note"
                >{{ t('expense.sourceDate', { date: date(row.document_issued_on) }) }}</small
              >
            </div>
          </td>
          <td role="cell" :data-label="headers[amortization ? 0 : 1]" class="expense-primary">
            <div>
              <template v-if="amortization"
                ><strong>{{
                  matched(row) ? row.asset_description || t('nav.assets') : t('expense.unmatched')
                }}</strong
                ><small>{{ row.counterparty_name || row.description || '' }}</small
                ><small
                  ><DocumentLabel
                    :row="row"
                    :period="period"
                    :return-url="returnUrl"
                    :navigate="navigate"
                  /><template v-if="row.document_issued_on">
                    ·
                    {{ t('expense.sourceDate', { date: date(row.document_issued_on) }) }}</template
                  ></small
                ><small>{{
                  t(
                    hasAmount(row.document_amount_eur)
                      ? 'expense.sourceAmount'
                      : 'expense.recordedAmount',
                    {
                      amount: money(
                        hasAmount(row.document_amount_eur)
                          ? row.document_amount_eur
                          : row.amount_eur,
                      ),
                    },
                  )
                }}</small
                ><small v-if="matched(row) && row.asset_match_method === 'inferred'">{{
                  t('expense.inferred')
                }}</small></template
              >
              <template v-else
                ><strong>{{
                  row.counterparty_name || row.description || t('transactions.noCounterparty')
                }}</strong
                ><small
                  ><DocumentLabel
                    :row="row"
                    :period="period"
                    :return-url="returnUrl"
                    :navigate="navigate" /></small
                ><small
                  v-if="
                    hasAmount(row.document_amount_eur) && row.document_amount_eur !== row.amount_eur
                  "
                  >{{
                    t('expense.sourceAmount', { amount: money(row.document_amount_eur) })
                  }}</small
                ></template
              >
              <div class="transaction-actions">
                <a
                  v-if="row.document_id"
                  class="text-button"
                  :href="`/api/document/${encodeURIComponent(row.document_id)}/content`"
                  target="_blank"
                  rel="noreferrer"
                  >{{ t('expense.source') }}</a
                ><a
                  v-if="matched(row)"
                  class="text-button"
                  href="/assets"
                  @click="followSpaLink($event, navigate)"
                  >{{ t('nav.assets') }}</a
                >
              </div>
            </div>
          </td>
          <td v-if="amortization" role="cell" :data-label="headers[1]">
            <div>
              <strong>{{ quarter(row.period_key) }}</strong
              ><small class="expense-note"
                >{{ t('expense.recognitionDate') }}: {{ date(row.transaction_date) }}</small
              >
            </div>
          </td>
          <td role="cell" :data-label="headers[2]">
            <div>
              <StatusCell :context="row.ui_context" /><small
                v-if="row.lifecycle_status === 'approved'"
                class="expense-note"
                >{{ t('expense.unposted') }}</small
              ><small v-if="row.is_future_dated === true" class="expense-note">{{
                t('expense.future', { date: date(row.transaction_date) })
              }}</small>
            </div>
          </td>
          <td role="cell" :data-label="headers[3]">
            <div>
              <strong>{{ money(amortization ? row.deductible_irpf_eur : row.amount_eur) }}</strong
              ><small
                v-if="!hasAmount(amortization ? row.deductible_irpf_eur : row.amount_eur)"
                class="expense-note"
                >{{ t('expense.missing') }}</small
              ><small
                v-if="
                  amortization &&
                  hasAmount(row.deductible_vat_eur) &&
                  Number(row.deductible_vat_eur) !== 0
                "
                class="expense-note"
                >IVA: {{ money(row.deductible_vat_eur) }}</small
              >
            </div>
          </td>
          <template v-if="!amortization"
            ><td role="cell" :data-label="headers[4]">
              <div>{{ money(row.deductible_irpf_eur) }}</div>
            </td>
            <td role="cell" data-label="IVA">
              <div>{{ money(row.deductible_vat_eur) }}</div>
            </td></template
          >
        </tr>
      </tbody>
    </table>
  </div>
</template>
