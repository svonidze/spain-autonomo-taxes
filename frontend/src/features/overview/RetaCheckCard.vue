<script setup lang="ts">
import { computed, onBeforeUnmount, shallowRef } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur, formatDateText } from '../../core/format.ts';
import { errorMessage } from '../../core/error-message.ts';
import { formatMessage, messageIds } from '../../core/i18n.ts';
import type { RetaCheck } from './model.ts';
// Decision support only: nothing here is added to or netted against tax cash due.
// A plain function type: the SFC compiler reads ViewServices['request'] as a Promise prop.
const props = defineProps<{ year: string; request: (url: string) => Promise<unknown> }>();
const { locale, t } = useLocale();
const data = shallowRef<RetaCheck>(),
  failure = shallowRef<unknown>();
let active = true,
  version = 0;
onBeforeUnmount(() => {
  active = false;
});
async function load() {
  const current = ++version;
  data.value = failure.value = undefined;
  try {
    const result = (await props.request(
      `/api/reta-check?year=${encodeURIComponent(props.year)}`,
    )) as RetaCheck;
    if (active && current === version) data.value = result;
  } catch (error) {
    if (active && current === version) failure.value = error ?? new Error('');
  }
}
// TaxesView unmounts this card whenever its period changes (and the shell
// remounts the screen on every route), so one fetch per mount is enough.
void load();
const tones = { ok: 'positive', below_bracket: 'attention', above_bracket: 'pending' } as const;
// An unexpected status reads as unknown, never as a success.
const status = computed(() => {
  const value = data.value?.status;
  return value === 'ok' || value === 'below_bracket' || value === 'above_bracket'
    ? value
    : 'unknown';
});
const tone = computed(() => (status.value === 'unknown' ? 'neutral' : tones[status.value]));
const money = (value?: number | null) => (value == null ? '—' : eur(value / 100, locale.value));
const day = (value?: string | null) => formatDateText(value, locale.value);
const reason = (code: string) =>
  messageIds.includes(`reta.reasons.${code}`)
    ? formatMessage(`reta.reasons.${code}`, {}, locale.value)
    : t('reta.reasons.other', { code });
</script>
<template>
  <section class="panel reta-check" aria-labelledby="reta-check-title">
    <header>
      <h2 id="reta-check-title">{{ t('reta.title', { year }) }}</h2>
      <span v-if="data" class="badge" :class="`status-${tone}`">{{
        t(`reta.status.${status}`)
      }}</span>
    </header>
    <p class="reta-check-note">{{ t('reta.disclaimer') }}</p>
    <p v-if="!data && !failure" role="status">{{ t('common.loading') }}</p>
    <div v-else-if="failure" role="alert">
      <p>{{ t('common.loadFailed') }}</p>
      <p class="reta-check-note">{{ errorMessage(failure, locale) }}</p>
      <button type="button" class="secondary-button" @click="load">{{ t('common.retry') }}</button>
    </div>
    <template v-else-if="data">
      <p role="status">
        {{ t(`reta.statusDetail.${status}`) }}
        <template v-if="data.through">{{ t('reta.window', { date: day(data.through) }) }}</template>
      </p>
      <template v-if="status === 'unknown' && data.reasons.length">
        <h3>{{ t('reta.reasonsTitle') }}</h3>
        <ul class="reta-check-reasons">
          <li v-for="code in data.reasons" :key="code">{{ reason(code) }}</li>
        </ul>
      </template>
      <dl v-if="data.bracket" class="tax-form-facts">
        <div>
          <dt>{{ t('reta.tramo') }}</dt>
          <dd>
            {{
              t('reta.tramoValue', { table: data.bracket.table, tramo: String(data.bracket.tramo) })
            }}
          </dd>
        </div>
        <div>
          <dt>{{ t('reta.baseRange') }}</dt>
          <dd>
            {{
              t('reta.range', {
                min: money(data.bracket.min_base_minor),
                max: money(data.bracket.max_base_minor),
              })
            }}
          </dd>
        </div>
        <div>
          <dt>{{ t('reta.monthlyIncome') }}</dt>
          <dd>{{ money(data.income?.monthly_average_minor) }}</dd>
        </div>
        <div>
          <dt>{{ t('reta.provisionalBase') }}</dt>
          <dd>{{ money(data.average_provisional_base_minor) }}</dd>
        </div>
        <div>
          <dt>{{ t('reta.additional') }}</dt>
          <dd data-reta="additional">{{ money(data.estimated_additional_minor) }}</dd>
        </div>
        <div>
          <dt>{{ t('reta.refund') }}</dt>
          <dd data-reta="refund">{{ money(data.estimated_refund_minor) }}</dd>
        </div>
        <div>
          <dt>{{ t('reta.lockedIn') }}</dt>
          <dd data-reta="locked">{{ money(data.additional_locked_in_minor) }}</dd>
        </div>
        <div v-if="data.next_base_change">
          <dt>{{ t('reta.nextChange') }}</dt>
          <dd>
            {{
              t('reta.nextChangeValue', {
                effective: day(data.next_base_change.effective_on),
                requestBy: day(data.next_base_change.request_by),
              })
            }}
          </dd>
        </div>
      </dl>
      <p v-if="data.boundary_sensitive" class="period-note posting-warning">
        {{ t('reta.boundarySensitive') }}
      </p>
      <h3>{{ t('reta.basesTitle') }}</h3>
      <div v-if="data.elections.length" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{{ t('reta.effectiveFrom') }}</th>
              <th>{{ t('reta.regimeLabel') }}</th>
              <th>{{ t('reta.monthlyBase') }}</th>
              <th>{{ t('reta.workerKindLabel') }}</th>
              <th>{{ t('reta.source') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in data.elections" :key="index">
              <td :data-label="t('reta.effectiveFrom')">{{ day(row.effective_from) }}</td>
              <td :data-label="t('reta.regimeLabel')">
                {{ t('reta.regime', { regime: row.regime }) }}
              </td>
              <td :data-label="t('reta.monthlyBase')">{{ money(row.monthly_base_minor) }}</td>
              <td :data-label="t('reta.workerKindLabel')">
                {{ t('reta.workerKind', { kind: row.worker_kind }) }}
              </td>
              <td :data-label="t('reta.source')">{{ row.source_reference }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else>{{ t('reta.basesEmpty') }}</p>
      <p class="reta-check-note">
        {{
          t('reta.basesHint', {
            command: 'autonomo-tax reta base add',
            doc: 'docs/user/RETA_CHECK.md',
          })
        }}
      </p>
    </template>
  </section>
</template>
