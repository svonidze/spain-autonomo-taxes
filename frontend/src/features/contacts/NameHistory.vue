<script setup lang="ts">
import { useLocale } from '../../vue/locale.ts';
import { localeTag } from '../../core/i18n.ts';
import type { NameChange } from './model.ts';
defineProps<{ changes: NameChange[] }>();
const { locale, t } = useLocale();
const time = (value: string) =>
  new Intl.DateTimeFormat(localeTag(locale.value), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
</script>
<template>
  <ol v-if="changes.length" class="counterparty-name-history-list">
    <li v-for="(change, index) in changes" :key="index">
      <div>
        {{ change.old_name }} → <strong>{{ change.new_name }}</strong>
      </div>
      <small
        >{{ time(change.changed_at) }} ·
        {{
          change.actor ||
          t(change.change_source === 'sheet' ? 'contacts.sheetActor' : 'contacts.localActor')
        }}</small
      >
    </li>
  </ol>
  <p v-else>{{ t('contacts.noNameHistory') }}</p>
</template>
