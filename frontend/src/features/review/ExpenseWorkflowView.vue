<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import {
  message,
  messageText,
  formatMessage,
  messageIds,
  type UiMessage,
} from '../../core/i18n.ts';
import { operationRequest } from '../../core/operation-request.ts';
import type { Counterparty } from '../contacts/model.ts';
import WorkflowField from './WorkflowField.vue';
import {
  defaults,
  factsFields,
  taxFields,
  technicalFields,
  fieldValue,
  setField,
  type ExpensePayload,
  type WorkflowContext,
  type WorkflowDraft,
  type WorkflowPreview,
  type PostedExpense,
  type Scalar,
  type FieldSpec,
} from './workflow-model.ts';
const props = defineProps<{ context: WorkflowContext }>();
const { locale, t } = useLocale();
const root = ref<HTMLElement>();
const draft = shallowRef<WorkflowDraft>(),
  payload = ref<ExpensePayload>();
const suppliers = shallowRef<Counterparty[]>([]),
  proposed = shallowRef<WorkflowPreview>(),
  posted = shallowRef<PostedExpense>();
const error = shallowRef<unknown>(),
  notice = shallowRef<UiMessage | string>(''),
  busy = ref(false);
const supplierSearch = ref(''),
  settlementRate = ref(''),
  settlementReference = ref(''),
  technicalOpen = ref(false);
let active = true,
  generation = 0,
  request: ReturnType<typeof operationRequest> | null = null;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const base = () => `/api/expense-workflows/${encodeURIComponent(props.context.transactionId)}`;
const current = (revision: number) => active && generation === revision;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
async function load() {
  const revision = ++generation,
    context = props.context;
  draft.value = undefined;
  payload.value = undefined;
  proposed.value = undefined;
  posted.value = undefined;
  error.value = undefined;
  try {
    const [value, parties] = await Promise.all([
      context.services.request(base()) as Promise<WorkflowDraft>,
      context.services.request('/api/counterparties') as Promise<Counterparty[]>,
    ]);
    if (!current(revision)) return;
    if (value.source.assets.length) {
      context.onLegacy();
      return;
    }
    if (!value.editable) {
      context.onPosted({ transaction_id: context.transactionId });
      return;
    }
    draft.value = value;
    payload.value = clone(value.payload);
    suppliers.value = parties;
    defaults(payload.value);
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) context.settled();
  }
}
watch(
  () => props.context,
  (context, previous) => {
    if (draft.value && payload.value && previous?.transactionId === context.transactionId) return;
    busy.value = false;
    notice.value = '';
    request = null;
    supplierSearch.value = '';
    settlementRate.value = '';
    settlementReference.value = '';
    technicalOpen.value = false;
    void load();
  },
  { immediate: true },
);
const retryLoad = () =>
  error.value instanceof ApiError && error.value.code === 'session_forbidden'
    ? window.location.reload()
    : void load();
const tax = computed(() => payload.value!.decision.tax_treatment);
const manual = computed(() =>
  draft.value?.source.issues.some((issue) =>
    ['document_structural_review', 'document_classification_review'].includes(issue.issue_code),
  ),
);
const sourceUrl = computed(
  () =>
    `/api/document/${encodeURIComponent(draft.value?.source.document.document_id || '')}/content`,
);
const visibleTaxes = computed(() =>
  taxFields.filter(
    (field) => !payload.value?.asset || !field.path.endsWith('deductible_irpf_minor'),
  ),
);
const allowedTaxes = computed(() =>
  (draft.value?.allowed_values.tax_code || []).filter(
    (code) =>
      ![
        'domestic_output',
        'domestic_output_zero',
        'eu_goods_income',
        'eu_service_income',
        'export',
        'outside_scope',
      ].includes(code),
  ),
);
const taxLabel = (code: string) =>
  messageIds.includes(`taxCodeLabels.${code}`)
    ? formatMessage(`taxCodeLabels.${code}`, {}, locale.value)
    : code;
function invalidate() {
  proposed.value = undefined;
  request = null;
}
function update(path: string, value: Scalar) {
  if (busy.value || !payload.value) return;
  setField(payload.value, path, value);
  invalidate();
  if (path === 'decision.tax_treatment.tax_code') {
    tax.value.include_modelo130 = !payload.value.asset;
    tax.value.include_modelo303 = true;
    tax.value.include_modelo347 = true;
    tax.value.aeat_operation_key = ['eu_service_expense', 'eu_goods_expense'].includes(
      String(value),
    )
      ? '09'
      : '01';
    tax.value.aeat_reverse_charge = [
      'eu_service_expense',
      'eu_goods_expense',
      'non_eu_service_expense',
      'domestic_reverse_charge_expense',
    ].includes(String(value));
  }
  if (path === 'facts.counterparty_id') payload.value.supplier = null;
  if (path === 'asset.method' && payload.value.asset)
    payload.value.asset.annual_rate_basis_points = value === 'immediate' ? 10000 : null;
  if (path === 'facts.currency') {
    payload.value.facts.currency = String(value).toUpperCase();
    payload.value.fx = null;
  }
}
function select(path: string, event: Event, boolean = false) {
  const value = (event.target as HTMLSelectElement).value;
  update(path, value === '' ? (boolean ? null : '') : boolean ? value === 'true' : value);
}
function check(path: string, event: Event) {
  update(path, (event.target as HTMLInputElement).checked);
}
function setKind(event: Event) {
  if (!payload.value || busy.value) return;
  payload.value.asset =
    (event.target as HTMLSelectElement).value === 'asset'
      ? {
          description: '',
          basis_minor: tax.value.taxable_base_minor,
          business_use_ratio: 1,
          annual_rate_basis_points: 10000,
          placed_in_service_on: payload.value.facts.transaction_date,
          method: 'immediate',
          new_equipment: false,
          aeat_asset_type: '23',
        }
      : null;
  invalidate();
}
function newSupplier() {
  if (!payload.value || busy.value) return;
  payload.value.facts.counterparty_id = null;
  payload.value.supplier = { display_name: '', tax_id: '', vat_id: '', country_code: '' };
  invalidate();
}
async function task(callback: (revision: number) => Promise<void>) {
  if (busy.value || !active) return;
  const revision = generation;
  busy.value = true;
  error.value = undefined;
  try {
    await callback(revision);
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      busy.value = false;
      await nextTick();
      if (error.value) root.value?.querySelector<HTMLElement>('[role="alert"]')?.focus();
    }
  }
}
async function save(revision: number) {
  const result = (await props.context.services.request(base() + '/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      payload: payload.value,
      expected_version: draft.value!.draft_version,
      source_snapshot_hash: draft.value!.source_snapshot_hash,
    }),
  })) as WorkflowDraft;
  if (!current(revision)) return false;
  draft.value = result;
  payload.value = clone(result.payload);
  notice.value = message('workflow.draftSavedOnTheServer');
  return true;
}
const saveOnly = () =>
  task(async (revision) => {
    if (await save(revision)) invalidate();
  });
const preview = () =>
  task(async (revision) => {
    if (!(await save(revision))) return;
    const value = (await props.context.services.request(base() + '/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_version: draft.value!.draft_version }),
    })) as WorkflowPreview;
    if (!current(revision)) return;
    if (typeof value.preview_token !== 'string' || !value.preview_token)
      throw new Error('review.invalidWorkItem');
    proposed.value = value;
    request = operationRequest(
      'expense-post:' + props.context.transactionId,
      value.preview_token,
      draft.value!.draft_version,
    );
  });
const confirm = () =>
  task(async (revision) => {
    if (!request || !proposed.value) return;
    const result = (await props.context.services.request(base() + '/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    })) as PostedExpense;
    if (current(revision)) {
      if (result.transaction_id !== props.context.transactionId)
        throw new Error('review.invalidWorkItem');
      posted.value = result;
      notice.value = '';
    }
  });
const followUp = () =>
  task(async (revision) => {
    const result = (await props.context.services.request(base() + '/follow-up', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })) as Partial<PostedExpense>;
    if (current(revision)) posted.value = { ...posted.value!, ...result };
  });
function reset() {
  if (
    !draft.value ||
    busy.value ||
    !window.confirm(t('workflow.replaceFieldsWithCurrentSourceValuesTheSavedDraft'))
  )
    return;
  payload.value = clone(draft.value.source_values);
  draft.value = {
    ...draft.value,
    source_snapshot_hash: draft.value.current_snapshot_hash,
    conflict: false,
  };
  defaults(payload.value);
  invalidate();
}
function useFx(settlement = false) {
  if (!payload.value || busy.value) return;
  const suggestion = draft.value?.fx_suggestion;
  if (!settlement && !['exact', 'prior'].includes(suggestion?.status || '')) return;
  payload.value.fx = settlement
    ? {
        rate_date: payload.value.facts.transaction_date,
        rate: settlementRate.value,
        rate_source: 'actual_settlement',
        source_reference: settlementReference.value,
        raw_observation: JSON.stringify({ operator_confirmed: true }),
        supersedes_rate_id: null,
      }
    : {
        rate_date: suggestion?.rate_date,
        rate: suggestion?.eur_per_unit,
        rate_source: 'ecb',
        source_reference: suggestion?.source_reference,
        raw_observation: suggestion?.raw_observation,
        supersedes_rate_id: null,
      };
  invalidate();
}
const money = (value: number | null | undefined) =>
  value == null ? '—' : (Number(value) / 100).toFixed(2);
const supplierFields: FieldSpec[] = [
  { path: 'supplier.display_name', label: 'workflow.name' },
  { path: 'supplier.country_code', label: 'workflow.countryEsUs' },
  { path: 'supplier.tax_id', label: 'workflow.taxIdentifier' },
  { path: 'supplier.vat_id', label: 'fields.vatId' },
];
const purposeFields: FieldSpec[] = [
  { path: 'decision.business_purpose', label: 'workflow.businessPurpose' },
  { path: 'decision.reason', label: 'workflow.decisionReason' },
  { path: 'change_reason', label: 'workflow.reasonForFactCorrectionInvalidatingApproval' },
];
const assetFields: FieldSpec[] = [
  { path: 'asset.description', label: 'workflow.description' },
  { path: 'asset.placed_in_service_on', label: 'workflow.inServiceDate', type: 'date' },
  {
    path: 'asset.basis_minor',
    label: 'workflow.amortizableBasisBeforeBusinessShareEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'asset.business_use_ratio',
    label: 'workflow.confirmedBusinessUse',
    kind: 'ratio',
    type: 'number',
  },
];
const supplierHidden = (party: Counterparty) =>
  !!supplierSearch.value &&
  party.counterparty_id !== payload.value?.facts.counterparty_id &&
  !`${party.display_name} · ${party.tax_id || party.vat_id || '—'}`
    .toLocaleLowerCase()
    .includes(supplierSearch.value.toLocaleLowerCase());
</script>
<template>
  <section ref="root" class="section-stack expense-workflow">
    <header class="panel-header">
      <h2>{{ t('workflow.reviewAndPostExpense') }}</h2>
      <span>{{ draft?.source.period.period_key }}</span>
    </header>
    <div class="wf-message" role="status">{{ messageText(notice, locale) }}</div>
    <div v-if="error" class="review-alert error" role="alert" tabindex="-1">
      {{ errorMessage(error, locale)
      }}<button v-if="!draft" type="button" class="secondary-button" @click="retryLoad">
        {{
          t(
            error instanceof ApiError && error.code === 'session_forbidden'
              ? 'common.reload'
              : 'common.retry',
          )
        }}
      </button>
    </div>
    <p v-if="!draft && !error" role="status">{{ t('common.loading') }}</p>
    <div v-if="draft?.conflict" class="review-alert warning">
      {{ t('workflow.sourceChangedTheSavedDraftIsRetainedCompareIt')
      }}<button type="button" id="wf-reset" :disabled="busy" @click="reset">
        {{ t('workflow.useCurrentFacts') }}
      </button>
    </div>
    <section v-if="posted" class="panel wf-fields">
      <strong>{{ t('workflow.postedInLedger') }}</strong>
      <p>{{ t('workflow.thisIsNotAPaymentOrTaxFiling') }}</p>
      <template v-if="posted.follow_up_pending"
        ><p>{{ t('workflow.refreshOrInboxCleanupNeedsRetryDoNotRepost') }}</p>
        <button id="wf-follow-up" type="button" :disabled="busy" @click="followUp">
          {{ t('workflow.retryFollowUp') }}
        </button></template
      ><button id="wf-open-posted" type="button" :disabled="busy" @click="context.onPosted(posted)">
        {{ t('workflow.openExpense') }}
      </button>
    </section>
    <template v-else-if="draft && payload">
      <div class="wf-layout">
        <aside class="panel wf-source">
          <a :href="sourceUrl" target="_blank" rel="noreferrer">{{ t('workflow.openOriginal') }}</a
          ><iframe
            :title="t('workflow.sourceDocument')"
            :src="sourceUrl.replace(/content$/, 'preview')"
          ></iframe>
        </aside>
        <form id="wf-form" class="panel wf-fields" @submit.prevent="preview">
          <fieldset :disabled="busy">
            <h3>{{ t('workflow.documentFacts') }}</h3>
            <div class="wf-grid">
              <WorkflowField
                v-for="field in factsFields"
                :key="field.path"
                :field="field"
                :model-value="fieldValue(payload, field.path)"
                @update:model-value="update(field.path, $event)"
              />
            </div>
            <label
              ><span>{{ t('workflow.findSupplierByNameTaxId') }}</span
              ><input id="wf-supplier-search" v-model="supplierSearch" type="search"
            /></label>
            <label
              ><span>{{ t('workflow.supplier') }}</span
              ><select
                data-p="facts.counterparty_id"
                :value="payload.facts.counterparty_id ?? ''"
                @input="select('facts.counterparty_id', $event)"
              >
                <option value=""></option>
                <option
                  v-for="party in suppliers"
                  :key="party.counterparty_id"
                  :value="party.counterparty_id"
                  :hidden="supplierHidden(party)"
                >
                  {{ party.display_name }} · {{ party.tax_id || party.vat_id || '—' }}
                </option>
              </select></label
            >
            <button
              id="wf-new-supplier"
              class="secondary-button"
              type="button"
              @click="newSupplier"
            >
              {{ t('workflow.newSupplier') }}
            </button>
            <div v-if="payload.supplier" class="wf-grid">
              <WorkflowField
                v-for="field in supplierFields"
                :key="field.path"
                :field="field"
                :model-value="fieldValue(payload, field.path)"
                @update:model-value="update(field.path, $event)"
              />
            </div>
            <label
              ><span>{{ t('workflow.businessActivity') }}</span
              ><select
                data-p="facts.business_activity_id"
                :value="payload.facts.business_activity_id ?? ''"
                @input="select('facts.business_activity_id', $event)"
              >
                <option value=""></option>
                <option
                  v-for="activity in draft.activities"
                  :key="activity.business_activity_id"
                  :value="activity.business_activity_id"
                >
                  {{ activity.description }}
                </option>
              </select></label
            >
            <WorkflowField
              v-for="field in purposeFields"
              :key="field.path"
              :field="field"
              :model-value="fieldValue(payload, field.path)"
              @update:model-value="update(field.path, $event)"
            />
            <label class="wf-check"
              ><input
                type="checkbox"
                data-p="decision.document_valid"
                :checked="payload.decision.document_valid === true"
                @input="check('decision.document_valid', $event)"
              /><span>{{
                t('workflow.originalIsReadableFactsAndAmountsHaveBeenChecked')
              }}</span></label
            >
            <WorkflowField
              v-if="manual"
              :field="{
                path: 'manual_review_reason',
                label: 'workflow.manualReviewExplanationAfterExtractionIssue',
              }"
              :model-value="payload.manual_review_reason"
              @update:model-value="update('manual_review_reason', $event)"
            />
            <h3>{{ t('workflow.treatmentAndDeductionsEur') }}</h3>
            <label
              ><span>{{ t('workflow.expenseType') }}</span
              ><select id="wf-kind" :value="payload.asset ? 'asset' : 'ordinary'" @change="setKind">
                <option value="ordinary">{{ t('workflow.ordinaryExpense') }}</option>
                <option value="asset">{{ t('workflow.equipment') }}</option>
              </select></label
            >
            <label
              ><span>{{ t('workflow.taxClassification') }}</span
              ><select
                data-p="decision.tax_treatment.tax_code"
                :value="tax.tax_code ?? ''"
                @input="select('decision.tax_treatment.tax_code', $event)"
              >
                <option value=""></option>
                <option v-for="code in allowedTaxes" :key="code" :value="code">
                  {{ taxLabel(code) }}
                </option>
              </select></label
            >
            <div class="wf-grid">
              <WorkflowField
                v-for="field in visibleTaxes"
                :key="field.path"
                :field="field"
                :model-value="fieldValue(payload, field.path)"
                @update:model-value="update(field.path, $event)"
              />
            </div>
            <label
              ><span>{{ t('workflow.ivaPurchaseClassification') }}</span
              ><select
                data-p="decision.tax_treatment.vat_investment_good"
                :value="tax.vat_investment_good == null ? '' : String(tax.vat_investment_good)"
                @input="select('decision.tax_treatment.vat_investment_good', $event, true)"
              >
                <option value=""></option>
                <option value="false">{{ t('workflow.currentPurchaseForIva') }}</option>
                <option value="true">{{ t('workflow.ivaInvestmentGood') }}</option>
              </select></label
            ><small>{{
              t('workflow.irpfDepreciationDoesNotDetermineTheIvaInvestmentClassification')
            }}</small>
            <template v-if="payload.asset"
              ><h3>{{ t('workflow.equipmentAndSchedule') }}</h3>
              <div class="wf-grid">
                <WorkflowField
                  v-for="field in assetFields"
                  :key="field.path"
                  :field="field"
                  :model-value="fieldValue(payload, field.path)"
                  @update:model-value="update(field.path, $event)"
                />
                <label
                  ><span>{{ t('workflow.assetCategory') }}</span
                  ><select
                    data-p="asset.aeat_asset_type"
                    :value="payload.asset.aeat_asset_type ?? ''"
                    @input="select('asset.aeat_asset_type', $event)"
                  >
                    <option value=""></option>
                    <option
                      v-for="item in [
                        ['23', 'workflow.computerElectronic'],
                        ['21', 'workflow.machinery'],
                        ['24', 'workflow.furniture'],
                        ['25', 'workflow.installations'],
                        ['28', 'workflow.tools'],
                        ['29', 'workflow.otherEquipment'],
                      ]"
                      :key="item[0]"
                      :value="item[0]"
                    >
                      {{ formatMessage(item[1], {}, locale) }}
                    </option>
                  </select></label
                >
                <label
                  ><span>{{ t('workflow.depreciation') }}</span
                  ><select
                    data-p="asset.method"
                    :value="payload.asset.method ?? ''"
                    @input="select('asset.method', $event)"
                  >
                    <option value=""></option>
                    <option value="immediate">
                      {{ t('workflow.immediateNewLowValueEquipment') }}
                    </option>
                    <option value="linear">{{ t('workflow.linearSchedule') }}</option>
                  </select></label
                >
                <WorkflowField
                  v-if="payload.asset.method === 'linear'"
                  :field="{
                    path: 'asset.annual_rate_basis_points',
                    label: 'workflow.reviewedAnnualRate',
                    kind: 'rate',
                    type: 'number',
                  }"
                  :model-value="payload.asset.annual_rate_basis_points"
                  @update:model-value="update('asset.annual_rate_basis_points', $event)"
                />
              </div>
              <label class="wf-check"
                ><input
                  type="checkbox"
                  data-p="asset.new_equipment"
                  :checked="payload.asset.new_equipment === true"
                  @input="check('asset.new_equipment', $event)"
                /><span>{{ t('workflow.equipmentWasAcquiredNew') }}</span></label
              ><small>{{
                t('workflow.futureDepreciationRemainsPlannedUntilExplicitlyPosted')
              }}</small></template
            >
            <section v-if="payload.facts.currency !== 'EUR'">
              <h3>{{ t('workflow.eurConversion') }}</h3>
              <p>
                {{
                  draft.fx_suggestion?.note ||
                  draft.fx_suggestion?.eur_per_unit ||
                  t('workflow.saveTheDraftToRefreshTheRateSuggestion')
                }}
              </p>
              <button
                id="wf-use-fx"
                type="button"
                :disabled="!['exact', 'prior'].includes(draft.fx_suggestion?.status || '')"
                @click="useFx()"
              >
                {{ t('workflow.confirmSuggestedEcbRate') }}</button
              ><label
                ><span>{{ t('workflow.documentedSettlementRate') }}</span
                ><input
                  id="wf-settlement-rate"
                  v-model="settlementRate"
                  type="number"
                  step="0.000001" /></label
              ><label
                ><span>{{ t('workflow.settlementEvidenceReference') }}</span
                ><input id="wf-settlement-ref" v-model="settlementReference" /></label
              ><button id="wf-settlement" type="button" @click="useFx(true)">
                {{ t('workflow.useSettlementRate') }}</button
              ><small>{{ payload.fx ? t('workflow.rateSelectedCheckTaxAmountsInEur') : '' }}</small>
            </section>
            <details
              :open="technicalOpen"
              @toggle="technicalOpen = ($event.target as HTMLDetailsElement).open"
            >
              <summary>{{ t('workflow.additionalTaxDecisionFields') }}</summary>
              <div class="wf-grid">
                <label
                  ><span>{{ t('workflow.aeatDocumentType') }}</span
                  ><select
                    data-p="decision.tax_treatment.aeat_invoice_type"
                    :value="tax.aeat_invoice_type ?? ''"
                    @input="select('decision.tax_treatment.aeat_invoice_type', $event)"
                  >
                    <option value=""></option>
                    <option
                      v-for="value in draft.allowed_values.aeat_invoice_type || []"
                      :key="value"
                      :value="value"
                    >
                      {{ value }}
                    </option>
                  </select></label
                ><WorkflowField
                  v-for="field in technicalFields"
                  :key="field.path"
                  :field="field"
                  :model-value="fieldValue(payload, field.path)"
                  @update:model-value="update(field.path, $event)"
                />
              </div>
              <label class="wf-check"
                ><input
                  type="checkbox"
                  data-p="decision.tax_treatment.aeat_reverse_charge"
                  :checked="tax.aeat_reverse_charge === true"
                  @input="check('decision.tax_treatment.aeat_reverse_charge', $event)"
                /><span>{{ t('workflow.reverseCharge') }}</span></label
              ><label v-for="form in [130, 303, 347]" :key="form" class="wf-check"
                ><input
                  type="checkbox"
                  :data-p="`decision.tax_treatment.include_modelo${form}`"
                  :checked="tax[`include_modelo${form}`] === true"
                  @input="check(`decision.tax_treatment.include_modelo${form}`, $event)"
                /><span>Modelo {{ form }}</span></label
              ><WorkflowField
                :field="{
                  path: 'decision.tax_treatment.notes',
                  label: 'workflow.decisionNoteAndSource',
                }"
                :model-value="tax.notes"
                @update:model-value="update('decision.tax_treatment.notes', $event)"
              />
            </details>
            <footer class="wf-actions">
              <button type="button" id="wf-save" class="secondary-button" @click="saveOnly">
                {{ t('workflow.saveDraft') }}</button
              ><button type="submit" class="primary-button" :disabled="draft.conflict">
                {{ t('workflow.previewResult') }}
              </button>
            </footer>
          </fieldset>
        </form>
      </div>
      <section
        v-if="proposed"
        id="wf-preview"
        class="panel wf-fields"
        :aria-label="t('workflow.postingPreview')"
      >
        <h3>{{ t('workflow.beforePosting') }}</h3>
        <p>{{ proposed.supplier }} · {{ proposed.period }}</p>
        <dl class="review-facts-list">
          <div>
            <dt>{{ t('workflow.purchase') }}</dt>
            <dd>{{ money(proposed.gross_minor) }} {{ proposed.currency }}</dd>
          </div>
          <div>
            <dt>IVA</dt>
            <dd>{{ money(proposed.deductible_vat_minor) }} EUR</dd>
          </div>
          <div>
            <dt>{{ t('workflow.irpfDeductionNow') }}</dt>
            <dd>{{ money(proposed.deductible_irpf_minor) }} EUR</dd>
          </div>
          <div>
            <dt>{{ t('workflow.laterDepreciation') }}</dt>
            <dd>{{ money(proposed.future_depreciation_minor) }} EUR</dd>
          </div>
        </dl>
        <div v-if="proposed.schedule.length" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{{ t('workflow.period') }}</th>
                <th>{{ t('workflow.recognitionDate') }}</th>
                <th>EUR</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in proposed.schedule" :key="index">
                <td>{{ row.period_key }}</td>
                <td>{{ row.recognition_on || '—' }}</td>
                <td>{{ money(row.amount_minor) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>{{ t('workflow.onlyThisExpenseAndTheShownDepreciationNoPayment') }}</p>
        <button
          id="wf-confirm"
          class="primary-button"
          type="button"
          :disabled="busy"
          @click="confirm"
        >
          {{ t('workflow.confirmAndPost') }}
        </button>
      </section>
    </template>
  </section>
</template>
