<script setup lang="ts">
import {useLocale} from '../vue/locale.ts';
import {helpAdapter} from '../vue/help.ts';
defineProps<{term: string; label?: string}>();
const {locale} = useLocale();
const help = helpAdapter();
</script>
<template><button type="button" class="term-help" :data-help-term="term" :aria-label="`${(locale, help.word('help'))}: ${label || term}`" aria-describedby="accounting-tooltip">?</button></template>
