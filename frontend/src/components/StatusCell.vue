<script setup lang="ts">
import {computed, onBeforeUnmount, watch} from 'vue';
import {useLocale} from '../vue/locale.ts';
import {helpAdapter, type HelpContext} from '../vue/help.ts';
const props = defineProps<{context: HelpContext}>();
const {locale} = useLocale();
const help = helpAdapter();
const scope = help.createScope(props.context);
watch(() => props.context, value => scope.update(value), {deep: true, flush: 'sync'});
onBeforeUnmount(() => scope.dispose());
const presentation = computed(() => {
  void locale.value;
  return {label: help.label(props.context), summary: help.summary(props.context), tone: help.tone(props.context), details: help.word('details')};
});
</script>
<template>
  <div class="status-context">
    <span class="badge" :class="`status-${presentation.tone}`">{{presentation.label}}</span><small>{{presentation.summary}}</small>
    <button type="button" class="text-button status-details-button" :data-status-help="scope.id" :data-status-subject="context.subject_id ? `${context.domain}:${context.subject_id}` : undefined" aria-haspopup="dialog">{{presentation.details}}</button>
  </div>
</template>
