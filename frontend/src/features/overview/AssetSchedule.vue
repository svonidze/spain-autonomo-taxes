<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { localeTag, message, messageText, type UiMessage } from '../../core/i18n.ts';
import { operationRequest } from '../../core/operation-request.ts';
import { errorMessage } from '../../core/error-message.ts';
import type { ViewServices } from '../../vue/services.ts';
import type { AssetScheduleData, ScheduleRow } from './model.ts';
const props = defineProps<{
  assetId: string;
  request: ViewServices['request'];
  refreshAssets: () => Promise<void>;
}>();
const { locale, t } = useLocale();
const data = shallowRef<AssetScheduleData>(),
  error = shallowRef<unknown>();
const status = shallowRef<UiMessage | string>('');
const busy = ref(false),
  postedButStale = ref(false);
let active = true,
  generation = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
const current = (revision: number) => active && revision === generation;
async function load() {
  const revision = ++generation;
  error.value = undefined;
  try {
    const result = (await props.request(
      `/api/assets/${encodeURIComponent(props.assetId)}/schedule`,
    )) as AssetScheduleData;
    if (current(revision)) {
      data.value = result;
      postedButStale.value = false;
    }
  } catch (failure) {
    if (current(revision)) {
      error.value = failure;
      throw failure;
    }
  }
}
watch(
  () => props.assetId,
  () => {
    data.value = undefined;
    status.value = '';
    void load().catch(() => {});
  },
  { immediate: true },
);
async function post(row: ScheduleRow) {
  if (busy.value || postedButStale.value) return;
  const revision = generation;
  busy.value = true;
  status.value = '';
  const pending = operationRequest(
    `depreciation-post:${row.amortization_entry_id}`,
    'depreciation',
    row.row_version,
  );
  let posted = false;
  try {
    const result = (await props.request(
      `/api/depreciation/${encodeURIComponent(row.amortization_entry_id)}/post`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: pending.request_id, expected_version: row.row_version }),
      },
    )) as { follow_up_pending?: boolean };
    posted = true;
    if (!current(revision)) return;
    await props.refreshAssets();
    if (!current(revision)) return;
    await load();
    if (active)
      status.value = message(
        result.follow_up_pending
          ? 'workflow.postedCalculationRefreshNeedsRetry'
          : 'workflow.depreciationPosted',
      );
  } catch (failure) {
    if (active) {
      postedButStale.value = posted;
      if (posted)
        status.value = message('workflow.depreciationPostedReloadThePageToRetrieveCurrentData');
      else status.value = errorMessage(failure, locale.value);
    }
  } finally {
    if (active) busy.value = false;
  }
}
const money = (value: number) =>
  new Intl.NumberFormat(localeTag(locale.value), {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value / 100);
</script>
<template>
  <section class="panel wf-fields">
    <h3>{{ t('workflow.scheduleAndRecognizedDepreciation') }}</h3>
    <div role="status" id="wf-schedule-status">{{ messageText(status, locale) }}</div>
    <p v-if="error && !data" role="alert">{{ errorMessage(error, locale) }}</p>
    <p v-else-if="!data" role="status">{{ t('common.loading') }}</p>
    <div v-if="data" class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{{ t('workflow.period') }}</th>
            <th>EUR</th>
            <th>{{ t('workflow.status') }}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.rows" :key="row.amortization_entry_id">
            <td>{{ row.period_key }}</td>
            <td>{{ money(row.amount_minor) }}</td>
            <td>
              {{
                t(
                  row.recognition_transaction_id
                    ? 'workflow.posted'
                    : data.native
                      ? 'workflow.planned'
                      : 'workflow.historicalRecord',
                )
              }}
            </td>
            <td>
              <button
                v-if="row.can_post"
                :data-recognize="row.amortization_entry_id"
                type="button"
                :disabled="busy || postedButStale"
                @click="post(row)"
              >
                {{ t('workflow.post') }} {{ row.period_key }}</button
              ><template v-else>{{ row.recognition_on || '' }}</template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
