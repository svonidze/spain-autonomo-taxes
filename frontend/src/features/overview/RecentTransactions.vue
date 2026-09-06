<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { quarterNumber } from '../../core/i18n.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import DocumentLabel from '../transactions/DocumentLabel.vue';
import { copyable, hasAmount, type TransactionRow } from '../transactions/model.ts';
const props = defineProps<{
  rows: TransactionRow[];
  period: string;
  target: string | null;
  navigate: (url: string) => void;
  copy: (row: TransactionRow) => void;
}>();
const { locale, t } = useLocale();
const date = (value?: string) => formatDateText(value, locale.value);
const minor = (value?: number | null) =>
  value == null ? t('help.words.missing') : eur(value / 100, locale.value);
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
const sourceUrl = () => `/dashboard?period=${encodeURIComponent(props.period)}`;
</script>
<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>{{ t('transactions.date') }}</th>
          <th>{{ t('transactions.counterpartyDocument') }}</th>
          <th>{{ t('transactions.status') }} <TermHelp term="posting" /></th>
          <th>{{ t('transactions.amount') }}</th>
          <th>{{ t('transactions.irpfDeduction') }} <TermHelp term="IRPF" /></th>
          <th>IVA <TermHelp term="IVA" /></th>
          <th>
            <span class="visually-hidden">{{ t('tables.actions') }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.transaction_id" :data-transaction-id="row.transaction_id">
          <td :data-label="t('transactions.date')">
            <template v-if="row.expense_kind === 'amortization'"
              ><strong>{{ quarter(row.period_key) }}</strong
              ><small class="expense-note"
                >{{ t('expense.recognitionDate') }}: {{ date(row.transaction_date) }}</small
              ></template
            ><template v-else>{{ date(row.transaction_date) }}</template>
          </td>
          <td class="cell-primary" :data-label="t('transactions.counterpartyDocument')">
            <template v-if="row.expense_kind === 'amortization'"
              ><strong>{{
                matched(row) ? row.asset_description || t('nav.assets') : t('expense.unmatched')
              }}</strong
              ><small>{{ row.counterparty_name || row.description || '' }}</small
              ><small
                ><DocumentLabel
                  :row="row"
                  :period="period"
                  :return-url="sourceUrl()"
                  :navigate="navigate"
                /><template v-if="row.document_issued_on">
                  · {{ t('expense.sourceDate', { date: date(row.document_issued_on) }) }}</template
                ></small
              ><small>{{
                t(
                  hasAmount(row.document_amount_eur)
                    ? 'expense.sourceAmount'
                    : 'expense.recordedAmount',
                  {
                    amount: eur(
                      hasAmount(row.document_amount_eur) ? row.document_amount_eur : row.amount_eur,
                      locale,
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
                  :return-url="sourceUrl()"
                  :navigate="navigate" /></small
            ></template>
          </td>
          <td :data-label="t('transactions.status')">
            <StatusCell :context="row.ui_context" /><template v-if="row.entry_type === 'expense'"
              ><small v-if="row.lifecycle_status === 'approved'" class="expense-note">{{
                t('expense.unposted')
              }}</small
              ><small v-if="row.is_future_dated === true" class="expense-note">{{
                t('expense.future', { date: date(row.transaction_date) })
              }}</small></template
            >
          </td>
          <td class="amount" :data-label="t('transactions.amount')">
            <template v-if="row.expense_kind === 'amortization'"
              >{{ eur(row.deductible_irpf_eur, locale)
              }}<small class="expense-note">{{ t('expense.quarterAmount') }}</small></template
            ><template v-else>{{
              row.amount_eur
                ? eur(row.amount_eur, locale)
                : `${row.amount_original || '—'} ${row.currency || ''}`
            }}</template>
          </td>
          <td class="amount" :data-label="t('transactions.irpfDeduction')">
            {{
              row.expense_kind === 'amortization'
                ? t('expense.inAmount')
                : minor(row.deductible_irpf_minor)
            }}
          </td>
          <td class="amount" data-label="IVA">{{ minor(row.deductible_vat_minor) }}</td>
          <td>
            <div class="transaction-actions">
              <a
                v-if="row.document_id"
                class="text-button"
                :href="`/api/document/${encodeURIComponent(row.document_id)}/content`"
                target="_blank"
                rel="noreferrer"
                >{{ t('common.file') }}</a
              ><button
                v-if="copyable(row, target)"
                type="button"
                class="copy-action-button"
                :data-copy-transaction-id="row.transaction_id"
                :title="t('common.copyToPeriod', { period: target! })"
                :aria-label="t('common.copyToPeriod', { period: target! })"
                @click="copy(row)"
              >
                <svg
                  class="copy-action-icon"
                  width="20"
                  height="20"
                  viewBox="0 0 20 20"
                  aria-hidden="true"
                  focusable="false"
                >
                  <path
                    d="M7.75 2.75h6a2.5 2.5 0 0 1 2.5 2.5v6"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                  <rect
                    x="5.25"
                    y="5.25"
                    width="9.5"
                    height="11"
                    rx="2.5"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                  />
                  <path
                    d="M5.25 8.25h-1a2.5 2.5 0 0 1-2.5-2.5v-1a2.5 2.5 0 0 1 2.5-2.5h6.5"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </button>
            </div>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="7">
            <div class="empty-state">{{ t('common.noRecords') }}</div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
