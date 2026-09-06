<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { ApiError } from '../../core/http.ts';
import StatusCell from '../../components/StatusCell.vue';
import TermHelp from '../../components/TermHelp.vue';
import ChartHost from '../../charts/ChartHost.vue';
import AssetSchedule from './AssetSchedule.vue';
import type { AssetRow, OverviewContext } from './model.ts';
const props = defineProps<{ context: OverviewContext }>();
const { locale, t } = useLocale();
const rows = shallowRef<AssetRow[]>([]),
  error = shallowRef<unknown>(),
  loading = ref(true),
  selected = ref('');
let active = true,
  generation = 0;
onBeforeUnmount(() => {
  active = false;
  generation++;
});
async function refreshAssets() {
  const revision = generation;
  const result = (await props.context.services.request(
    `/api/assets?period=${encodeURIComponent(props.context.period)}`,
  )) as AssetRow[];
  if (active && revision === generation) rows.value = result;
}
async function load() {
  const revision = ++generation;
  loading.value = true;
  error.value = undefined;
  selected.value = '';
  try {
    await refreshAssets();
  } catch (failure) {
    if (active && revision === generation) error.value = failure;
  } finally {
    if (active && revision === generation) {
      loading.value = false;
      props.context.settled();
    }
  }
}
watch(
  () => props.context,
  () => void load(),
  { immediate: true },
);
const money = (minor: number | null | undefined) =>
  minor == null ? t('help.words.missing') : eur(minor / 100, locale.value);
const forbidden = () => error.value instanceof ApiError && error.value.code === 'session_forbidden';
const retry = () => (forbidden() ? window.location.reload() : void load());
</script>
<template>
  <div>
    <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="empty-state state-error" role="alert">
      <p>{{ t(forbidden() ? 'common.sessionExpired' : 'common.loadFailed') }}</p>
      <button class="secondary-button" @click="retry">
        {{ t(forbidden() ? 'common.reload' : 'common.retry') }}
      </button>
      <details>
        <summary>{{ t('issues.sourceDetails') }}</summary>
        <p>{{ errorMessage(error, locale) }}</p>
      </details>
    </div>
    <template v-else>
      <div class="table-toolbar">
        <div>
          <h2>{{ t('assets.title') }}</h2>
          <p>{{ t('help.words.allAssets') }}: {{ context.period }}</p>
        </div>
      </div>
      <section class="panel">
        <div class="table-wrap">
          <table class="asset-explanations-table">
            <thead>
              <tr>
                <th>{{ t('assets.asset') }}</th>
                <th>{{ t('assets.inService') }}</th>
                <th>
                  {{ t('assets.cost') }} <TermHelp term="cost" /> / {{ t('assets.base') }}
                  <TermHelp term="base" />
                </th>
                <th>
                  {{ t('assets.businessUse') }} <TermHelp term="use" /> / {{ t('assets.rate') }}
                  <TermHelp term="rate" />
                </th>
                <th>
                  {{ t('help.words.scope') }} {{ context.period }} <TermHelp term="forecast" />
                </th>
                <th>{{ t('assets.decision') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.asset_id">
                <td class="cell-primary" :data-label="t('assets.asset')">
                  <strong>{{ row.description || row.asset_code }}</strong
                  ><small>{{ row.source_invoice_number || '' }}</small>
                </td>
                <td :data-label="t('assets.inService')">
                  {{ formatDateText(row.placed_in_service_on, locale) }}
                </td>
                <td :data-label="t('assets.cost')">
                  {{ money(row.cost_minor)
                  }}<small class="value-detail"
                    >{{ t('assets.base') }}: {{ money(row.amortizable_base_minor) }}</small
                  >
                </td>
                <td :data-label="t('assets.businessUse')">
                  {{ row.business_use_percent == null ? '—' : `${row.business_use_percent}%`
                  }}<small class="value-detail"
                    >{{ t('assets.rate') }}:
                    {{
                      row.annual_rate_percent == null ? '—' : `${row.annual_rate_percent}%`
                    }}</small
                  >
                </td>
                <td :data-label="t('help.words.scope')">
                  <div v-if="row.ui_context.amortization?.count" class="amortization-summary">
                    <span
                      v-if="row.ui_context.amortization.rows.some((row) => row.include_in_books)"
                      >{{ t('help.words.book') }}:
                      <strong>{{ money(row.ui_context.amortization.book_minor) }}</strong></span
                    ><span
                      v-if="row.ui_context.amortization.rows.some((row) => !row.include_in_books)"
                      >{{ t('help.words.excluded') }}:
                      <strong>{{ money(row.ui_context.amortization.excluded_minor) }}</strong></span
                    ><small
                      v-if="
                        row.ui_context.amortization.rows.some(
                          (row) => row.entry_kind === 'adjustment',
                        )
                      "
                      >{{ t('help.words.adjustment') }}:
                      {{ money(row.ui_context.amortization.adjustment_minor) }}</small
                    >
                  </div>
                  <span v-else>{{ t('help.words.none') }}</span>
                </td>
                <td :data-label="t('assets.decision')"><StatusCell :context="row.ui_context" /></td>
              </tr>
              <tr v-if="!rows.length">
                <td colspan="6">
                  <div class="empty-state">{{ t('common.noRecords') }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <ChartHost
          id="chart-amortization"
          kind="amortization"
          :period="context.period"
          :request="context.services.request"
        />
      </section>
      <section class="panel wf-fields">
        <label
          ><span>{{ t('app.inline.equipmentSchedule') }}</span
          ><select id="asset-plan-select" v-model="selected">
            <option value=""></option>
            <option v-for="row in rows" :key="row.asset_id" :value="row.asset_id">
              {{ row.description || row.asset_code }}
            </option>
          </select></label
        >
        <div id="asset-plan-detail">
          <AssetSchedule
            v-if="selected"
            :key="selected"
            :asset-id="selected"
            :request="context.services.request"
            :refresh-assets="refreshAssets"
          />
        </div>
      </section>
    </template>
  </div>
</template>
