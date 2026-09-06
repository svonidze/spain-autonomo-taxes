<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { errorMessage } from '../../core/error-message.ts';
import { message, messageText, type UiMessage } from '../../core/i18n.ts';
import {
  currencies,
  loadDraft,
  readValues,
  saveDraft,
  rawDraft,
  clearUnchangedDraft,
  isGoogleDriveUrl,
  intakeRequest,
  intakeResult,
  type IntakeServices,
  type IntakeOptions,
  type IntakeKind,
  type IntakeSource,
  type IntakeResult,
  type Folder,
  type PickerConfig,
} from './model.ts';
import { loadPicker, chooseFolder } from './picker.ts';
const props = defineProps<{ services: IntakeServices }>();
const { locale, t } = useLocale();
const dialog = ref<HTMLDialogElement>(),
  form = ref<HTMLFormElement>(),
  fileInput = ref<HTMLInputElement>(),
  driveInput = ref<HTMLInputElement>();
const kind = ref<IntakeKind>('expense_invoice'),
  source = ref<IntakeSource>('upload'),
  period = ref(''),
  busy = ref(false),
  pickerBusy = ref(false),
  pickerEnabled = ref(false),
  dragging = ref(false);
const accepted = ref(false),
  locked = computed(() => busy.value || accepted.value);
const selectedFile = shallowRef<File>(),
  folder = shallowRef<Folder>(),
  status = shallowRef<UiMessage | string>(''),
  failure = shallowRef<unknown>(),
  notices = shallowRef<(UiMessage | string)[]>([]);
const blank = () => ({
  issued_on: '',
  counterparty_name: '',
  document_number: '',
  currency: 'EUR',
  gross: '',
  taxable_base: '',
  vat: '',
  drive_url: '',
});
const values = reactive(blank());
let touched = false,
  active = true,
  session = 0,
  draftTimer: ReturnType<typeof setTimeout> | undefined,
  successTimer: ReturnType<typeof setTimeout> | undefined,
  picker: ReturnType<typeof chooseFolder> | undefined;
const owns = (id: number) => active && session === id;
function flushDraft(force = false) {
  clearTimeout(draftTimer);
  if (form.value && (touched || force)) saveDraft(readValues(form.value.elements));
}
async function open(value: IntakeKind, options: IntakeOptions) {
  if (busy.value) {
    await nextTick();
    if (!dialog.value?.open) dialog.value?.showModal();
    return;
  }
  session++;
  touched = false;
  accepted.value = false;
  clearTimeout(successTimer);
  clearTimeout(draftTimer);
  picker?.close();
  picker = undefined;
  pickerBusy.value = false;
  form.value?.reset();
  if (fileInput.value) fileInput.value.value = '';
  selectedFile.value = undefined;
  folder.value = undefined;
  kind.value = value;
  source.value = 'upload';
  period.value = options.targetPeriodKey;
  failure.value = undefined;
  status.value = '';
  notices.value = options.noticeLines || [];
  Object.assign(values, blank());
  if (options.prefill)
    Object.assign(values, options.prefill, { currency: options.prefill.currency || 'EUR' });
  else {
    const saved = loadDraft();
    if (saved) {
      Object.assign(values, saved);
      notices.value = [message('intake.draftRestored')];
    }
  }
  await nextTick();
  if (!dialog.value?.open) dialog.value?.showModal();
  void availability(session);
}
function close() {
  clearTimeout(successTimer);
  if (!locked.value) flushDraft();
  if (!busy.value) {
    session++;
    pickerBusy.value = false;
  }
  dialog.value?.close();
  folder.value = undefined;
  picker?.close();
  picker = undefined;
}
async function availability(id: number) {
  try {
    const config = (await props.services.request('/api/google-picker/config')) as PickerConfig;
    if (owns(id)) pickerEnabled.value = Boolean(config?.enabled);
  } catch {
    if (owns(id)) pickerEnabled.value = false;
  }
}
function input() {
  if (locked.value) return;
  touched = true;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    if (active && dialog.value?.open) flushDraft();
  }, 250);
}
function changeKind(value: IntakeKind) {
  if (!locked.value) kind.value = value;
}
async function changeSource(value: IntakeSource) {
  if (locked.value) return;
  source.value = value;
  await nextTick();
  if (value === 'google_drive') driveInput.value?.focus();
}
function chooseFile() {
  if (locked.value) return;
  selectedFile.value = fileInput.value?.files?.[0];
  input();
}
function drag(event: DragEvent, inside: boolean) {
  event.preventDefault();
  if (!locked.value) dragging.value = inside;
}
function drop(event: DragEvent) {
  event.preventDefault();
  dragging.value = false;
  if (locked.value || !event.dataTransfer?.files.length || !fileInput.value) return;
  const data = new DataTransfer();
  data.items.add(event.dataTransfer.files[0]);
  fileInput.value.files = data.files;
  chooseFile();
}
async function chooseGoogleFolder() {
  if (locked.value || pickerBusy.value) return;
  const id = session,
    origin = props.services.locationKey(),
    trigger = document.activeElement as HTMLElement | null;
  let shown = false;
  pickerBusy.value = true;
  failure.value = undefined;
  try {
    const config = (await props.services.request('/api/google-picker/config')) as PickerConfig;
    if (!owns(id) || busy.value || !dialog.value?.open || source.value !== 'upload') return;
    if (!config.enabled || !config.developer_key || !config.app_id || !config.access_token)
      throw new Error('intake.googlePickerUnavailable');
    const api = await loadPicker();
    if (
      !owns(id) ||
      busy.value ||
      !dialog.value?.open ||
      source.value !== 'upload' ||
      props.services.locationKey() !== origin
    )
      return;
    // Native modal top-layer/inert behavior must not cover the external Picker.
    flushDraft();
    dialog.value.close();
    shown = true;
    const selectedPicker = chooseFolder(api, config, locale.value);
    picker = selectedPicker;
    const selected = await selectedPicker.result;
    selectedPicker.close();
    if (picker === selectedPicker) picker = undefined;
    if (owns(id) && props.services.locationKey() === origin) {
      if (selected) folder.value = selected;
      dialog.value?.showModal();
    }
  } catch (error) {
    if (owns(id) && !busy.value && props.services.locationKey() === origin) {
      failure.value = error;
      if (!dialog.value?.open) dialog.value?.showModal();
    }
  } finally {
    if (owns(id)) {
      pickerBusy.value = false;
      await nextTick();
      if (shown && dialog.value?.open && trigger?.isConnected)
        trigger.focus({ preventScroll: true });
    }
  }
}
async function submit() {
  if (locked.value || !form.value) return;
  if (source.value === 'upload' && !selectedFile.value) {
    status.value = message('intake.selectFile');
    return;
  }
  if (source.value === 'google_drive' && !isGoogleDriveUrl(values.drive_url)) {
    status.value = message('intake.googleDriveInvalid');
    driveInput.value?.focus();
    return;
  }
  flushDraft(true);
  const draft = rawDraft(),
    id = session,
    origin = props.services.locationKey();
  const expected = { kind: kind.value, period: period.value };
  const data = new FormData(form.value);
  data.set('kind', expected.kind);
  data.set('period', expected.period);
  const request = intakeRequest(data, source.value, values.drive_url, folder.value);
  busy.value = true;
  failure.value = undefined;
  status.value = message('intake.processing');
  try {
    const result = intakeResult(
      await props.services.request(request.url, {
        ...request.options,
        fallbackMessage: t('intake.failed'),
      }),
      expected,
    );
    if (!owns(id)) return;
    clearUnchangedDraft(draft);
    accepted.value = true;
    status.value = message('intake.accepted', {
      id: result.system_marker ? String(result.system_marker).slice(0, 8) : t('common.noId'),
    });
    props.services.notify(message('intake.acceptedToast', { period: result.period }));
    if (dialog.value?.open)
      successTimer = setTimeout(() => {
        if (!owns(id) || !dialog.value?.open) return;
        dialog.value.close();
        folder.value = undefined;
        if (props.services.locationKey() === origin) props.services.accepted(result);
      }, 700);
  } catch (error) {
    if (owns(id)) {
      failure.value = error;
      status.value = '';
    }
  } finally {
    if (owns(id)) busy.value = false;
  }
}
const hint = computed(() => {
  const gross = Number.parseFloat(values.gross),
    base = Number.parseFloat(values.taxable_base),
    vat = Number.parseFloat(values.vat);
  return kind.value === 'expense_invoice' &&
    [gross, base, vat].every(Number.isFinite) &&
    Math.abs(gross - (base + vat)) > 0.01
    ? t('intake.consistencyHint', { expected: (base + vat).toFixed(2), total: gross.toFixed(2) })
    : '';
});
const today = () => {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
};
async function focusDocumentNumber() {
  await nextTick();
  const field = form.value?.elements.namedItem('document_number') as HTMLInputElement | null;
  field?.focus();
  field?.select();
}
onBeforeUnmount(() => {
  active = false;
  session++;
  clearTimeout(draftTimer);
  clearTimeout(successTimer);
  picker?.close();
  dialog.value?.close();
});
defineExpose({ open, close, focusDocumentNumber, isBusy: () => busy.value });
</script>
<template>
  <dialog
    ref="dialog"
    id="vue-intake-dialog"
    class="intake-dialog"
    data-vue-owned
    @cancel.prevent="close"
  >
    <form ref="form" id="vue-intake-form" method="dialog" @submit.prevent="submit" @input="input">
      <header class="dialog-header">
        <div>
          <h2>{{ t('intake.title') }}</h2>
          <p id="vue-intake-period-label">{{ period }}</p>
        </div>
        <button
          type="button"
          class="icon-button"
          id="vue-close-dialog"
          :aria-label="t('common.close')"
          @click="close"
        >
          ×
        </button>
      </header>
      <div class="segmented-control" role="group" :aria-label="t('intake.kindAria')">
        <button
          type="button"
          data-kind="expense_invoice"
          :class="{ active: kind === 'expense_invoice' }"
          :disabled="locked"
          @click="changeKind('expense_invoice')"
        >
          {{ t('nav.expenses') }}</button
        ><button
          type="button"
          data-kind="income_invoice"
          :class="{ active: kind === 'income_invoice' }"
          :disabled="locked"
          @click="changeKind('income_invoice')"
        >
          {{ t('nav.income') }}
        </button>
      </div>
      <input type="hidden" name="kind" :value="kind" /><input
        type="hidden"
        name="period"
        :value="period"
      />
      <fieldset
        class="segmented-control intake-source-control"
        :aria-label="t('intake.sourceAria')"
        :disabled="locked"
      >
        <button
          type="button"
          data-intake-source="upload"
          :class="{ active: source === 'upload' }"
          :aria-pressed="source === 'upload'"
          @click="changeSource('upload')"
        >
          {{ t('intake.sourceUpload') }}</button
        ><button
          type="button"
          data-intake-source="google_drive"
          :class="{ active: source === 'google_drive' }"
          :aria-pressed="source === 'google_drive'"
          @click="changeSource('google_drive')"
        >
          {{ t('intake.sourceGoogleDrive') }}
        </button>
      </fieldset>
      <label
        class="file-drop"
        id="vue-file-drop"
        :class="{ dragging }"
        :hidden="source === 'google_drive'"
        @dragenter="drag($event, true)"
        @dragover="drag($event, true)"
        @dragleave="drag($event, false)"
        @drop="drop"
        ><input
          ref="fileInput"
          type="file"
          name="file"
          id="vue-intake-file"
          :required="source === 'upload'"
          :disabled="source === 'google_drive' || locked"
          accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.csv,.txt"
          @change="chooseFile"
        /><strong id="vue-file-label">{{
          selectedFile?.name ||
          t(kind === 'income_invoice' ? 'intake.incomeFile' : 'intake.expenseFile')
        }}</strong
        ><span>{{ t('intake.fileFormats') }}</span></label
      >
      <div
        id="vue-google-folder-controls"
        class="intake-folder-controls"
        :hidden="source !== 'upload' || !pickerEnabled"
      >
        <button
          type="button"
          id="vue-choose-google-folder"
          class="secondary-button"
          :disabled="locked || pickerBusy"
          @click="chooseGoogleFolder"
        >
          {{ t('intake.chooseGoogleFolder') }}</button
        ><span aria-live="polite">{{
          folder ? t('intake.googleFolderSelected', { name: folder.name }) : ''
        }}</span>
      </div>
      <label id="vue-google-drive-url-field" :hidden="source !== 'google_drive'"
        ><span>{{ t('intake.googleDriveUrl') }}</span
        ><input
          ref="driveInput"
          v-model="values.drive_url"
          type="url"
          name="drive_url"
          id="vue-google-drive-url"
          inputmode="url"
          autocomplete="url"
          :placeholder="t('intake.googleDrivePlaceholder')"
          :required="source === 'google_drive'"
          :disabled="source !== 'google_drive' || locked"
        /><small>{{ t('intake.googleDriveHint') }}</small></label
      >
      <p class="intake-notice" :hidden="source !== 'google_drive'">
        {{ t('intake.googlePickerHint') }}
      </p>
      <div class="form-grid">
        <label
          ><span>{{ t('fields.documentDate') }}</span
          ><input
            v-model="values.issued_on"
            type="date"
            name="issued_on"
            :max="today()"
            :disabled="locked" /></label
        ><label
          ><span>{{ t(kind === 'income_invoice' ? 'fields.client' : 'fields.supplier') }}</span
          ><input
            v-model="values.counterparty_name"
            type="text"
            name="counterparty_name"
            autocomplete="organization"
            :disabled="locked" /></label
        ><label
          ><span>{{ t('fields.number') }}</span
          ><input
            v-model="values.document_number"
            type="text"
            name="document_number"
            aria-describedby="vue-intake-notice"
            :disabled="locked" /></label
        ><label
          ><span>{{ t('fields.currency') }}</span
          ><select v-model="values.currency" name="currency" :disabled="locked">
            <option v-for="code in currencies" :key="code" :value="code">{{ code }}</option>
          </select></label
        ><label
          ><span>{{ t('fields.total') }}</span
          ><input
            v-model="values.gross"
            type="number"
            name="gross"
            min="0"
            step="0.01"
            inputmode="decimal"
            :disabled="locked" /></label
        ><label class="expense-only" :hidden="kind === 'income_invoice'"
          ><span>{{ t('fields.vatBase') }}</span
          ><input
            v-model="values.taxable_base"
            type="number"
            name="taxable_base"
            min="0"
            step="0.01"
            inputmode="decimal"
            :disabled="locked" /></label
        ><label class="expense-only" :hidden="kind === 'income_invoice'"
          ><span>IVA</span
          ><input
            v-model="values.vat"
            type="number"
            name="vat"
            min="0"
            step="0.01"
            inputmode="decimal"
            :disabled="locked"
        /></label>
      </div>
      <p id="vue-intake-consistency-hint" class="intake-notice" aria-live="polite" :hidden="!hint">
        {{ hint }}
      </p>
      <div
        id="vue-intake-notice"
        class="intake-notice"
        role="note"
        aria-live="polite"
        :hidden="!notices.length"
      >
        <p v-for="(notice, index) in notices" :key="index">{{ messageText(notice, locale) }}</p>
      </div>
      <footer class="dialog-actions">
        <span id="vue-intake-status" role="status">{{
          failure ? errorMessage(failure, locale) : messageText(status, locale)
        }}</span
        ><button type="button" class="secondary-button" id="vue-cancel-dialog" @click="close">
          {{ t('common.cancel') }}</button
        ><button type="submit" class="primary-button" id="vue-submit-intake" :disabled="locked">
          {{ t('intake.accept') }}
        </button>
      </footer>
    </form>
  </dialog>
</template>
