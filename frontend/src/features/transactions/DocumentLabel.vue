<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { followSpaLink } from '../../vue/services.ts';
import type { TransactionRow } from './model.ts';
const props = defineProps<{
  row: TransactionRow;
  period: string;
  returnUrl: string;
  navigate: (url: string) => void;
}>();
const { t } = useLocale();
const url = () =>
  `/expenses/${encodeURIComponent(props.row.transaction_id)}?${new URLSearchParams({ period: props.period, returnTo: props.returnUrl })}`;
</script>
<template>
  <a
    v-if="
      row.entry_type === 'expense' &&
      ['posted', 'included_in_snapshot'].includes(row.lifecycle_status)
    "
    class="expense-document-link"
    :href="url()"
    @click="followSpaLink($event, navigate)"
    >{{ row.document_number || t('expense.open') }}</a
  >
  <template v-else>{{ row.document_number || row.description || '' }}</template>
</template>
