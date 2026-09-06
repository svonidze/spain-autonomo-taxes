<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import { copyable, type TransactionRow } from './model.ts';
const props = defineProps<{
  rows: TransactionRow[];
  target: string | null;
  query: string;
  copy: (row: TransactionRow) => void;
}>();
const { locale, t } = useLocale();
const minor = (value: number | null | undefined) =>
  value == null ? t('help.words.missing') : eur(value / 100, locale.value);
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
            {{ formatDateText(row.transaction_date, locale) }}
          </td>
          <td class="cell-primary" :data-label="t('transactions.counterpartyDocument')">
            <strong>{{
              row.counterparty_name || row.description || t('transactions.noCounterparty')
            }}</strong
            ><small>{{ row.document_number || row.description || '' }}</small>
          </td>
          <td :data-label="t('transactions.status')"><StatusCell :context="row.ui_context" /></td>
          <td class="amount" :data-label="t('transactions.amount')">
            {{
              row.amount_eur
                ? eur(row.amount_eur, locale)
                : `${row.amount_original || '—'} ${row.currency || ''}`
            }}
          </td>
          <td class="amount" :data-label="t('transactions.irpfDeduction')">
            {{ minor(row.deductible_irpf_minor) }}
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
                @click="props.copy(row)"
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
            <div class="empty-state">
              {{ t(query.trim() ? 'transactions.noMatches' : 'transactions.emptyIncome') }}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
