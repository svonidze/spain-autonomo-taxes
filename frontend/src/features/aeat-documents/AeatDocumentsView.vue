<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { formatDateText } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { formatMessage } from '../../core/i18n.ts';
import type { AeatDocumentRow, AeatDocumentsContext, AeatDocumentsData } from './model.ts';

const props = defineProps<{ context: AeatDocumentsContext }>();
const { locale, t } = useLocale();
const data = shallowRef<AeatDocumentsData>();
const error = shallowRef<unknown>();
const loading = ref(true);
const saving = ref(false);
const preview = shallowRef<Record<string, unknown>>();
const file = shallowRef<File>();
const filters = reactive({ year: '', form: '', kind: '', status: '', q: '' });
const upload = reactive({
  title: '',
  procedure_kind: 'roi_registration',
  procedure_code: 'G322',
  form_code: '036',
  document_kind: 'submission_receipt',
  status: 'submitted',
  occurred_at: '',
  requested_effective_on: '',
  reference: '',
  submission_reference: '',
  justificante: '',
  verification_code: '',
  notes: '',
});
const statusDialog = ref<HTMLDialogElement>();
const selected = shallowRef<AeatDocumentRow>();
const statusForm = reactive({
  status: 'information_requested',
  occurred_at: new Date().toISOString().slice(0, 16),
  evidence_reference: '',
  notes: '',
});
const statusTransitions: Record<string, string[]> = {
  submitted: ['information_requested', 'approved', 'rejected', 'closed'],
  information_requested: ['responded', 'approved', 'rejected', 'closed'],
  responded: ['information_requested', 'approved', 'rejected', 'closed'],
};
const nextStatuses = computed(() => statusTransitions[selected.value?.status || ''] || []);
let active = true;
onBeforeUnmount(() => {
  active = false;
});

async function load() {
  loading.value = !data.value;
  error.value = undefined;
  try {
    const result = (await props.context.services.request(
      '/api/aeat-documents',
    )) as AeatDocumentsData;
    if (active) data.value = result;
  } catch (failure) {
    if (active) error.value = failure;
  } finally {
    if (active) {
      loading.value = false;
      props.context.settled();
    }
  }
}
void load();

const rows = computed(() =>
  (data.value?.rows || []).filter((row) => {
    if (filters.year && row.occurred_at.slice(0, 4) !== filters.year) return false;
    if (filters.form && row.form_code !== filters.form) return false;
    if (filters.kind && row.document_kind !== filters.kind) return false;
    if (filters.status && row.status !== filters.status) return false;
    const q = filters.q.trim().toLocaleLowerCase();
    if (!q) return true;
    return [
      row.title,
      row.form_code,
      row.procedure_kind,
      row.procedure_code,
      row.submission_reference,
      row.justificante_number,
      row.verification_code,
      row.period_key,
    ]
      .join(' ')
      .toLocaleLowerCase()
      .includes(q);
  }),
);
const unique = (values: Array<string | null | undefined>) =>
  [...new Set(values.filter((value): value is string => Boolean(value)))].sort().reverse();
const years = computed(() =>
  unique((data.value?.rows || []).map((row) => row.occurred_at.slice(0, 4))),
);
const forms = computed(() => unique((data.value?.rows || []).map((row) => row.form_code)));
const label = (key: string) => formatMessage(key, {}, locale.value);

function uploadData(dryRun: boolean) {
  const body = new FormData();
  if (!file.value) throw new Error(t('aeatDocuments.fileRequired'));
  body.append('file', file.value);
  Object.entries(upload).forEach(([key, value]) => {
    if (value) body.append(key, value);
  });
  if (dryRun) body.append('dry_run', '1');
  return body;
}
function invalidatePreview() {
  preview.value = undefined;
}
async function submit(dryRun: boolean) {
  if (saving.value) return;
  saving.value = true;
  try {
    const result = (await props.context.services.request('/api/aeat-documents', {
      method: 'POST',
      body: uploadData(dryRun),
    })) as Record<string, unknown>;
    if (dryRun) preview.value = (result.candidate || result) as Record<string, unknown>;
    else {
      preview.value = undefined;
      props.context.notify(t('aeatDocuments.saved'));
      await load();
    }
  } catch (failure) {
    props.context.notify(errorMessage(failure, locale.value), true);
  } finally {
    saving.value = false;
  }
}
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0];
  invalidatePreview();
}
function openStatus(row: AeatDocumentRow) {
  if (!row.aeat_case_id || row.case_row_version == null) return;
  selected.value = row;
  statusForm.status = statusTransitions[row.status]?.[0] || 'closed';
  statusForm.occurred_at = new Date().toISOString().slice(0, 16);
  statusForm.evidence_reference = '';
  statusForm.notes = '';
  statusDialog.value?.showModal();
}
async function saveStatus() {
  const row = selected.value;
  if (!row?.aeat_case_id || row.case_row_version == null || saving.value) return;
  saving.value = true;
  try {
    await props.context.services.request(
      `/api/aeat-cases/${encodeURIComponent(row.aeat_case_id)}/status`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...statusForm,
          occurred_at: new Date(statusForm.occurred_at).toISOString(),
          notes: statusForm.notes || null,
          expected_row_version: row.case_row_version,
        }),
      },
    );
    statusDialog.value?.close();
    props.context.notify(t('aeatDocuments.statusSaved'));
    await load();
  } catch (failure) {
    props.context.notify(errorMessage(failure, locale.value), true);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="section-stack aeat-documents-page">
    <section class="panel">
      <header class="panel-header">
        <div>
          <h2>{{ t('aeatDocuments.title') }}</h2>
          <p>{{ t('aeatDocuments.intro') }}</p>
        </div>
        <small>{{ rows.length }}</small>
      </header>
      <div class="table-toolbar aeat-filter-grid">
        <label
          ><span>{{ t('aeatDocuments.search') }}</span
          ><input v-model="filters.q" type="search"
        /></label>
        <label
          ><span>{{ t('aeatDocuments.year') }}</span
          ><select v-model="filters.year">
            <option value="">{{ t('common.all') }}</option>
            <option v-for="value in years" :key="value">{{ value }}</option>
          </select></label
        >
        <label
          ><span>Modelo</span
          ><select v-model="filters.form">
            <option value="">{{ t('common.all') }}</option>
            <option v-for="value in forms" :key="value">{{ value }}</option>
          </select></label
        >
        <label
          ><span>{{ t('aeatDocuments.kind') }}</span
          ><select v-model="filters.kind">
            <option value="">{{ t('common.all') }}</option>
            <option
              v-for="value in [
                'submission_receipt',
                'authority_request',
                'response_receipt',
                'resolution',
                'certificate',
                'other',
              ]"
              :key="value"
              :value="value"
            >
              {{ label(`aeatDocuments.kind.${value}`) }}
            </option>
          </select></label
        >
        <label
          ><span>{{ t('aeatDocuments.status') }}</span
          ><select v-model="filters.status">
            <option value="">{{ t('common.all') }}</option>
            <option
              v-for="value in [
                'submitted',
                'information_requested',
                'responded',
                'approved',
                'rejected',
                'closed',
              ]"
              :key="value"
              :value="value"
            >
              {{ label(`aeatDocuments.status.${value}`) }}
            </option>
          </select></label
        >
      </div>
      <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
      <div v-else-if="error" class="empty-state state-error" role="alert">
        {{ errorMessage(error, locale) }}
      </div>
      <div v-else class="table-wrap">
        <table class="aeat-documents-table">
          <thead>
            <tr>
              <th>{{ t('aeatDocuments.date') }}</th>
              <th>Modelo</th>
              <th>{{ t('aeatDocuments.procedure') }}</th>
              <th>{{ t('aeatDocuments.kind') }}</th>
              <th>{{ t('aeatDocuments.status') }}</th>
              <th>Referencia</th>
              <th>Justificante</th>
              <th>CSV</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="`${row.record_source}:${row.record_id}`">
              <td :data-label="t('aeatDocuments.date')">
                {{ formatDateText(row.occurred_at, locale) }}
              </td>
              <td data-label="Modelo">{{ row.form_code || '—' }}</td>
              <td :data-label="t('aeatDocuments.procedure')">
                <strong>{{ row.title }}</strong
                ><small>{{ row.procedure_code || row.period_key || '' }}</small>
                <details v-if="row.status_history?.length" class="aeat-history">
                  <summary>{{ t('aeatDocuments.history') }}</summary>
                  <ol>
                    <li
                      v-for="event in row.status_history"
                      :key="`${event.status}:${event.occurred_at}`"
                    >
                      <time :datetime="event.occurred_at">{{
                        formatDateText(event.occurred_at, locale)
                      }}</time>
                      {{ label(`aeatDocuments.status.${event.status}`) }}
                      <small>{{ event.evidence_reference || event.notes || '' }}</small>
                    </li>
                  </ol>
                </details>
              </td>
              <td :data-label="t('aeatDocuments.kind')">
                {{ label(`aeatDocuments.kind.${row.document_kind}`) }}
              </td>
              <td :data-label="t('aeatDocuments.status')">
                {{ label(`aeatDocuments.status.${row.status}`) }}
              </td>
              <td data-label="Referencia">{{ row.submission_reference || '—' }}</td>
              <td data-label="Justificante">{{ row.justificante_number || '—' }}</td>
              <td data-label="CSV">
                <code>{{ row.verification_code || '—' }}</code>
              </td>
              <td class="aeat-actions">
                <a
                  v-if="row.content_url && row.source_available"
                  class="text-button"
                  :href="row.content_url"
                  target="_blank"
                  rel="noreferrer"
                  >{{ t('common.file') }}</a
                >
                <span v-else>{{ t('aeatDocuments.sourceUnavailable') }}</span>
                <button
                  v-if="
                    row.aeat_case_id && !['approved', 'rejected', 'closed'].includes(row.status)
                  "
                  type="button"
                  class="text-button"
                  @click="openStatus(row)"
                >
                  {{ t('aeatDocuments.addStatus') }}
                </button>
              </td>
            </tr>
            <tr v-if="!rows.length">
              <td colspan="9" class="empty-state">{{ t('common.noRecords') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <details class="panel aeat-upload-panel">
      <summary>{{ t('aeatDocuments.add') }}</summary>
      <form
        class="form-grid"
        @submit.prevent="submit(false)"
        @input="invalidatePreview"
        @change="invalidatePreview"
      >
        <label
          ><span>{{ t('aeatDocuments.pdf') }}</span
          ><input type="file" accept="application/pdf,.pdf" required @change="chooseFile"
        /></label>
        <label
          ><span>{{ t('aeatDocuments.caseTitle') }}</span
          ><input v-model="upload.title"
        /></label>
        <label
          ><span>{{ t('aeatDocuments.procedure') }}</span
          ><select v-model="upload.procedure_kind">
            <option value="roi_registration">ROI</option>
            <option value="periodic_filing">{{ t('aeatDocuments.procedure.periodic') }}</option>
            <option value="rectification">{{ t('aeatDocuments.procedure.rectification') }}</option>
            <option value="other">{{ t('aeatDocuments.procedure.other') }}</option>
          </select></label
        >
        <label
          ><span>{{ t('aeatDocuments.procedureCode') }}</span
          ><input v-model="upload.procedure_code"
        /></label>
        <label
          ><span>Modelo</span
          ><input v-model="upload.form_code" inputmode="numeric" pattern="[0-9]{3}"
        /></label>
        <label
          ><span>{{ t('aeatDocuments.kind') }}</span
          ><select v-model="upload.document_kind">
            <option
              v-for="value in [
                'submission_receipt',
                'authority_request',
                'response_receipt',
                'resolution',
                'certificate',
                'other',
              ]"
              :key="value"
              :value="value"
            >
              {{ label(`aeatDocuments.kind.${value}`) }}
            </option>
          </select></label
        >
        <label
          ><span>{{ t('aeatDocuments.status') }}</span
          ><select v-model="upload.status">
            <option
              v-for="value in [
                'submitted',
                'information_requested',
                'responded',
                'approved',
                'rejected',
                'closed',
              ]"
              :key="value"
              :value="value"
            >
              {{ label(`aeatDocuments.status.${value}`) }}
            </option>
          </select></label
        >
        <label
          ><span>{{ t('aeatDocuments.occurredAt') }}</span
          ><input v-model="upload.occurred_at" type="datetime-local" step="1"
        /></label>
        <label
          ><span>{{ t('aeatDocuments.requestedEffective') }}</span
          ><input v-model="upload.requested_effective_on" type="date"
        /></label>
        <label><span>Referencia</span><input v-model="upload.submission_reference" /></label>
        <label><span>Justificante</span><input v-model="upload.justificante" /></label>
        <label><span>CSV</span><input v-model="upload.verification_code" /></label>
        <label class="full-width"
          ><span>{{ t('aeatDocuments.notes') }}</span
          ><textarea v-model="upload.notes"></textarea>
        </label>
        <div class="dialog-actions full-width">
          <button
            type="button"
            class="secondary-button"
            :disabled="saving || !file"
            @click.stop="submit(true)"
          >
            {{ t('aeatDocuments.preview') }}
          </button>
          <button type="submit" class="primary-button" :disabled="saving || !preview">
            {{ t('aeatDocuments.save') }}
          </button>
        </div>
        <pre v-if="preview" class="full-width aeat-preview" aria-live="polite">{{
          JSON.stringify(preview, null, 2)
        }}</pre>
      </form>
    </details>

    <dialog ref="statusDialog" class="intake-dialog">
      <form @submit.prevent="saveStatus">
        <header class="dialog-header">
          <h2>{{ t('aeatDocuments.addStatus') }}</h2>
          <button
            type="button"
            class="icon-button"
            :aria-label="t('common.close')"
            @click="statusDialog?.close()"
          >
            ×
          </button>
        </header>
        <div class="form-grid">
          <label
            ><span>{{ t('aeatDocuments.status') }}</span
            ><select v-model="statusForm.status">
              <option v-for="value in nextStatuses" :key="value" :value="value">
                {{ label(`aeatDocuments.status.${value}`) }}
              </option>
            </select></label
          >
          <label
            ><span>{{ t('aeatDocuments.occurredAt') }}</span
            ><input v-model="statusForm.occurred_at" type="datetime-local" required
          /></label>
          <label class="full-width"
            ><span>{{ t('aeatDocuments.evidenceReference') }}</span
            ><input v-model="statusForm.evidence_reference" required
          /></label>
          <label class="full-width"
            ><span>{{ t('aeatDocuments.notes') }}</span
            ><textarea v-model="statusForm.notes"></textarea>
          </label>
        </div>
        <footer class="dialog-actions">
          <button type="button" class="secondary-button" @click="statusDialog?.close()">
            {{ t('common.cancel') }}</button
          ><button type="submit" class="primary-button" :disabled="saving">
            {{ t('common.save') }}
          </button>
        </footer>
      </form>
    </dialog>
  </div>
</template>
