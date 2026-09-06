<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { followSpaLink } from '../../vue/services.ts';
import StatusCell from '../../components/StatusCell.vue';
import { contactUrl, type ContactOperation } from './model.ts';
const props = defineProps<{
  rows: ContactOperation[];
  contactId: string;
  period: string;
  navigate: (url: string) => void;
}>();
const { locale, t } = useLocale();
const keys = [
  'transactions.date',
  'contacts.operationType',
  'contacts.description',
  'contacts.sourceDocument',
  'transactions.status',
  'transactions.amount',
] as const;
const expenseUrl = (row: ContactOperation) =>
  `/expenses/${encodeURIComponent(row.transaction_id)}?${new URLSearchParams({ period: row.period_key, returnTo: contactUrl(props.contactId, props.period) })}`;
const sourceAvailable = (row: ContactOperation) =>
  row.ui_context.documents?.some(
    (document) => document.document_id === row.document_id && document.available,
  );
const navigate = (event: MouseEvent) => followSpaLink(event, props.navigate);
</script>
<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th v-for="key in keys" :key="key">{{ t(key) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.transaction_id">
          <td :data-label="t(keys[0])">{{ formatDateText(row.transaction_date, locale) }}</td>
          <td :data-label="t(keys[1])">
            {{
              row.entry_type === 'income'
                ? t('nav.income')
                : row.entry_type === 'expense'
                  ? t('nav.expenses')
                  : row.entry_type
            }}
          </td>
          <td class="cell-primary" :data-label="t(keys[2])">
            <a v-if="row.entry_type === 'expense'" :href="expenseUrl(row)" @click="navigate">{{
              row.description || t('expense.title')
            }}</a
            ><template v-else>{{ row.description || '—' }}</template>
          </td>
          <td :data-label="t(keys[3])">
            <a
              v-if="sourceAvailable(row)"
              :href="`/api/document/${encodeURIComponent(row.document_id!)}/content`"
              target="_blank"
              rel="noreferrer"
              >{{ row.document_number || t('common.file') }}</a
            ><template v-else>{{ row.document_number || '—' }}</template>
          </td>
          <td :data-label="t(keys[4])">
            <StatusCell :context="row.ui_context" /><template v-if="row.entry_type === 'expense'"
              ><small v-if="row.lifecycle_status === 'approved'" class="expense-note">{{
                t('expense.unposted')
              }}</small
              ><small v-if="row.is_future_dated === true" class="expense-note">{{
                t('expense.future', { date: formatDateText(row.transaction_date, locale) })
              }}</small></template
            >
          </td>
          <td class="amount" :data-label="t(keys[5])">
            <template v-if="row.expense_kind === 'amortization'"
              >{{ eur(row.deductible_irpf_eur, locale)
              }}<small class="expense-note">{{ t('expense.quarterAmount') }}</small></template
            ><template v-else>{{
              row.amount_eur != null
                ? eur(row.amount_eur, locale)
                : `${row.amount_original ?? '—'} ${row.currency || ''}`
            }}</template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
