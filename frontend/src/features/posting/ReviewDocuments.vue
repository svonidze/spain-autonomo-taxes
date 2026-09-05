<script setup lang="ts">
import {computed} from 'vue';
import {useLocale} from '../../vue/locale.ts';
import {eur,formatDateText} from '../../core/format.ts';
import {formatMessage,messageIds} from '../../core/i18n.ts';
import type {HelpContext} from '../../vue/help.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import type {ReviewDocument} from './model.ts';
const props=defineProps<{documents:ReviewDocument[];issues:{ui_context:HelpContext}[]}>();const {locale,t}=useLocale();
const rows=computed(()=>props.documents.filter(row=>['received','extracted','needs_review'].includes(row.lifecycle_status)));
const type=(value:string)=>{const code=String(value || 'other_document');return messageIds.includes(`documents.types.${code}`)?formatMessage(`documents.types.${code}`,{},locale.value):code.replaceAll('_',' ');};
</script>
<template><section class="panel"><header class="panel-header"><h2>{{t('review.documents')}}</h2><small>{{rows.length}}</small></header><div class="table-wrap"><table><thead><tr><th>{{t('transactions.date')}}</th><th>{{t('documents.counterparty')}}</th><th>{{t('fields.number')}}</th><th>{{t('documents.type')}}</th><th>{{t('transactions.status')}} <TermHelp term="posting"/></th><th>{{t('transactions.amount')}}</th><th><span class="visually-hidden">{{t('tables.actions')}}</span></th></tr></thead><tbody><tr v-for="row in rows" :key="row.document_id"><td :data-label="t('transactions.date')">{{formatDateText(row.issued_on,locale)}}</td><td :data-label="t('documents.counterparty')">{{row.counterparty_name || '—'}}</td><td :data-label="t('fields.number')">{{row.document_number || '—'}}</td><td :data-label="t('documents.type')">{{type(row.document_type)}}</td><td :data-label="t('transactions.status')"><StatusCell :context="row.ui_context"/></td><td class="amount" :data-label="t('transactions.amount')">{{row.total_eur?eur(row.total_eur,locale):'—'}}</td><td :data-label="t('tables.actions')"><a v-if="row.source_available" class="text-button" :href="`/api/document/${encodeURIComponent(row.document_id)}/content`" target="_blank" rel="noreferrer">{{t('common.file')}}</a></td></tr><tr v-if="!rows.length"><td colspan="7"><div class="empty-state">{{t('common.noRecords')}}</div></td></tr></tbody></table></div></section>
<section class="panel"><header class="panel-header"><h2>{{t('review.openIssues')}}</h2><small>{{issues.length}}</small></header><ul v-if="issues.length" class="issues-list"><li v-for="(issue,index) in issues" :key="issue.ui_context.subject_id || index"><StatusCell :context="issue.ui_context"/></li></ul><div v-else class="empty-state">{{t('issues.none')}}</div></section></template>
