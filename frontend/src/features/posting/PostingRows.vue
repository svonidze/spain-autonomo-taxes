<script setup lang="ts">
import {useLocale} from '../../vue/locale.ts';
import {helpAdapter} from '../../vue/help.ts';
import {eur,formatDateText} from '../../core/format.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import {helpContext} from './normalize.ts';
import type {PostingItem} from './model.ts';
defineProps<{title:string;rows:PostingItem[]}>();const {locale,t}=useLocale();const help=helpAdapter();
const explanation=(row:PostingItem)=>{void locale.value;return row.structuredReasons.length?row.structuredReasons.map(reason=>help.text(help.reasonInfo(reason)[0])).join(' · '):t('issues.default');};
</script>
<template><div v-if="rows.length"><h3 class="posting-section-label">{{title}}</h3><div class="table-wrap"><table><thead><tr><th>{{t('transactions.date')}}</th><th>{{t('transactions.counterpartyDocument')}}</th><th>{{t('transactions.status')}} <TermHelp term="posting"/></th><th>{{t('transactions.amount')}}</th><th>{{t('review.postingResultMessage')}}</th></tr></thead><tbody>
  <tr v-for="(row,index) in rows" :key="`${row.transactionId || row.reviewId}:${index}`"><td :data-label="t('transactions.date')">{{formatDateText(row.transactionDate,locale)}}</td><td class="cell-primary" :data-label="t('transactions.counterpartyDocument')"><strong>{{row.description || row.reviewId || row.transactionId || t('common.noId')}}</strong><small>{{row.entryType || '—'}}</small></td><td :data-label="t('transactions.status')"><StatusCell :context="helpContext(row)"/></td><td class="amount" :data-label="t('transactions.amount')">{{row.amountEur?eur(row.amountEur,locale):'—'}}</td><td class="posting-result-message" :data-label="t('review.postingResultMessage')">{{explanation(row)}}</td></tr>
</tbody></table></div></div></template>
