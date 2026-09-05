<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, ref, shallowRef} from 'vue';
import {useLocale} from '../../vue/locale.ts';
import {errorMessage} from '../../core/error-message.ts';
import {ApiError} from '../../core/http.ts';
import {message, messageText, type UiMessage} from '../../core/i18n.ts';
import type {ViewServices} from '../../vue/services.ts';
import {validName, type Counterparty, type NameChange} from './model.ts';
import NameHistory from './NameHistory.vue';
const props = defineProps<{services: ViewServices}>();
const emit = defineEmits<{saved: [result: Partial<Counterparty> & {changed: boolean}, id: string]}>();
const {locale, t} = useLocale();
const dialog = ref<HTMLDialogElement>(), input = ref<HTMLInputElement>();
const row = shallowRef<Counterparty>(), conflict = shallowRef<Partial<Counterparty>>();
const name = ref(''), busy = ref(false), invalid = ref(false);
const error = shallowRef<UiMessage | string>('');
const history = shallowRef<NameChange[]>(), historyError = ref(false), historyOpen = ref(false);
let active = true, sequence = 0, historyRequest = 0, opener: HTMLElement | undefined;
onBeforeUnmount(() => {active = false; sequence++; dialog.value?.close();});
const dirty = computed(() => !!row.value && name.value !== row.value.display_name);
async function open(value: Counterparty, trigger: HTMLElement) {
  row.value = {...value}; name.value = value.display_name; opener = trigger;
  conflict.value = undefined; error.value = ''; invalid.value = false; historyOpen.value = false;
  sequence++; await nextTick(); dialog.value!.showModal(); input.value!.focus(); void loadHistory();
}
function close(force = false) {
  if (!row.value) return true;
  if (!force && (busy.value || (dirty.value && !window.confirm(t('contacts.discardName'))))) return false;
  sequence++; row.value = undefined; dialog.value?.close(); if (opener?.isConnected) opener.focus(); return true;
}
async function loadHistory() {
  const owner = row.value, revision = ++historyRequest;
  if (!owner) return;
  history.value = undefined; historyError.value = false;
  try {
    const result = await props.services.request(`/api/counterparties/${encodeURIComponent(owner.counterparty_id)}/name-history`) as {changes: NameChange[]};
    if (active && row.value === owner && revision === historyRequest) history.value = result.changes;
  } catch {if (active && row.value === owner && revision === historyRequest) historyError.value = true;}
}
function acceptCurrent() {
  if (!row.value || !conflict.value || busy.value) return;
  row.value = {...row.value, ...conflict.value}; conflict.value = undefined; error.value = '';
  input.value?.focus(); void loadHistory();
}
async function save() {
  const owner = row.value, revision = sequence;
  if (!owner || busy.value || conflict.value) return;
  invalid.value = !validName(name.value); error.value = '';
  if (invalid.value) {error.value = message('contacts.invalidName'); input.value?.focus(); return;}
  busy.value = true;
  try {
    const result = await props.services.request(`/api/counterparties/${encodeURIComponent(owner.counterparty_id)}/rename`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({display_name: name.value, expected_row_version: owner.row_version})}) as Partial<Counterparty> & {changed: boolean};
    if (!active || revision !== sequence) return;
    close(true); emit('saved', result, owner.counterparty_id);
  } catch (failure) {
    if (!active || revision !== sequence) return;
    if (failure instanceof ApiError && failure.status === 409 && failure.code === 'stale_counterparty' && failure.current) {
      conflict.value = failure.current as Partial<Counterparty>; error.value = message('contacts.nameConflict');
    } else if (failure instanceof ApiError && failure.code === 'invalid_name') error.value = message('contacts.invalidName');
    else if (failure instanceof ApiError && failure.code === 'counterparty_busy') error.value = message('contacts.nameBusy');
    else if (failure instanceof ApiError && failure.status === 404) error.value = message('contacts.nameMissing');
    else error.value = errorMessage(failure, locale.value);
  } finally {if (active) busy.value = false;}
}
defineExpose({open, canLeave: () => close(), isDirty: () => dirty.value, isBusy: () => busy.value});
</script>
<template>
  <Teleport to="body"><dialog ref="dialog" id="vue-counterparty-name-dialog" class="intake-dialog counterparty-name-dialog" aria-labelledby="vue-counterparty-name-title" data-vue-owned @cancel.prevent="close()">
    <form v-if="row" novalidate @submit.prevent="save">
      <header class="dialog-header"><h2 id="vue-counterparty-name-title">{{t('contacts.editName')}}</h2><button type="button" class="icon-button" :aria-label="t('common.close')" :disabled="busy" @click="close()">×</button></header>
      <p id="vue-counterparty-name-hint">{{t('contacts.renameHint')}}</p>
      <div class="form-grid counterparty-name-fields"><label for="vue-counterparty-name-input"><span>{{t('contacts.nameLabel')}}</span><input ref="input" id="vue-counterparty-name-input" v-model="name" type="text" required :disabled="busy" :aria-invalid="invalid || undefined" aria-describedby="vue-counterparty-name-hint vue-counterparty-name-error"></label></div>
      <p id="vue-counterparty-name-error" class="counterparty-name-error" role="alert">{{messageText(error, locale)}}</p>
      <div v-if="conflict" class="counterparty-name-conflict"><p>{{t('contacts.currentName', {name: conflict.display_name || ''})}}</p><button type="button" class="secondary-button" @click="acceptCurrent">{{t('contacts.acceptCurrentName')}}</button></div>
      <details :open="historyOpen" @toggle="historyOpen = ($event.target as HTMLDetailsElement).open"><summary>{{t('contacts.nameHistory')}}</summary>
        <div aria-live="polite"><NameHistory v-if="history" :changes="history"/><p v-else>{{t(historyError ? 'contacts.historyError' : 'common.loading')}}</p></div>
        <button v-if="historyError" type="button" class="secondary-button" @click="loadHistory">{{t('contacts.retryHistory')}}</button>
      </details>
      <footer class="dialog-actions"><button type="button" class="secondary-button" :disabled="busy" @click="close()">{{t('common.cancel')}}</button><button type="submit" class="primary-button" :disabled="busy || !!conflict">{{t(busy ? 'contacts.savingName' : 'contacts.saveName')}}</button></footer>
    </form>
  </dialog></Teleport>
</template>
