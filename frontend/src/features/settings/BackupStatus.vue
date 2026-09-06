<script setup lang="ts">
import {computed} from 'vue';
import {useLocale} from '../../vue/locale.ts';
import {localeTag} from '../../core/i18n.ts';
import type {Backups} from './model.ts';
const props=defineProps<{backups:Backups}>();const {locale,t}=useLocale();
const kinds=['daily','monthly'] as const;
function date(value:string){const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'—':new Intl.DateTimeFormat(localeTag(locale.value),{dateStyle:'medium',timeStyle:'short'}).format(parsed);}
const verification=computed(()=>props.backups.recovery_verification?.monthly);
</script>
<template>
  <h3>{{t('settings.form.lastSuccess')}}</h3><div class="settings-runs"><article v-for="kind in kinds" :key="kind" class="settings-run"><h4>{{t(`settings.form.${kind}`)}}</h4>
    <template v-if="backups.last_success?.[kind]"><p><time :datetime="backups.last_success[kind]!.recorded_at">{{date(backups.last_success[kind]!.recorded_at)}}</time></p><p>{{backups.last_success[kind]!.keep===null?t('settings.form.appliedUnknown'):t('settings.form.applied',{count:backups.last_success[kind]!.keep!})}}</p><p>{{t(backups.last_success[kind]!.offsite_status==='acknowledged'?'settings.form.uploadAcknowledged':backups.last_success[kind]!.offsite_status==='pending'?'settings.form.uploadPending':backups.last_success[kind]!.offsite_status==='failed'?'settings.form.uploadFailed':backups.last_success[kind]!.offsite_status==='disabled'?'settings.form.uploadDisabled':backups.last_success[kind]!.offsite?'settings.form.offsiteYes':'settings.form.offsiteNo')}}</p></template><p v-else>{{t('settings.form.unknown')}}</p>
  </article></div>
  <h3>{{t('settings.form.recoveryCheck')}}</h3><div class="settings-runs"><template v-if="verification?.last_attempt"><p>{{t(verification.last_attempt.status==='success'?'settings.form.recoverySuccess':verification.last_attempt.status==='running'?'settings.form.recoveryRunning':'settings.form.recoveryFailed')}}</p><p><time :datetime="verification.last_attempt.recorded_at">{{date(verification.last_attempt.recorded_at)}}</time></p><p v-if="verification.last_success&&verification.last_attempt.status!=='success'">{{t('settings.form.recoveryPrevious',{date:date(verification.last_success.recorded_at)})}}</p></template><p v-else>{{t('settings.form.recoveryUnknown')}}</p></div>
  <p v-if="!Object.values(backups.last_success||{}).some(run=>run?.settings_format===1)" class="settings-hint">{{t('settings.form.consumerUnknown')}}</p>
</template>
