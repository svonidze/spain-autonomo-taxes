<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { followSpaLink } from '../../vue/services.ts';
import { formatDateText } from '../../core/format.ts';
import { ApiError } from '../../core/http.ts';
import { errorMessage } from '../../core/error-message.ts';
import { formatMessage, message, messageIds } from '../../core/i18n.ts';
import ChartHost from '../../charts/ChartHost.vue';
import StatusCell from '../../components/StatusCell.vue';
import NameHistory from './NameHistory.vue';
import RenameDialog from './RenameDialog.vue';
import ContactOperations from './ContactOperations.vue';
import {
  contactUrl,
  type ContactsContext,
  type Counterparty,
  type ContactOperation,
  type ContactPage,
  type NameChange,
} from './model.ts';
const props = defineProps<{ context: ContactsContext }>();
const { locale, t } = useLocale();
const rows = shallowRef<Counterparty[]>([]),
  party = shallowRef<Counterparty>();
const chartRevision = ref(0);
const periods = ref<string[]>([]),
  loading = ref(true),
  error = shallowRef<unknown>();
const operations = shallowRef<ContactOperation[]>([]),
  operationBusy = ref(false),
  operationError = ref(false);
const total = ref<number | null>(null),
  hasMore = ref(false);
let nextOffset = 0,
  generation = 0,
  historyRequest = 0,
  active = true;
const history = shallowRef<NameChange[]>(),
  historyError = ref(false),
  historyOpen = ref(false);
const rename = ref<InstanceType<typeof RenameDialog>>();
const menu = shallowRef<{ row: Counterparty; trigger: HTMLElement; top: number; left: number }>();
const menuElement = ref<HTMLElement>(),
  menuStyle = ref({ left: '0px', top: '0px' });
const listColumns = [
  'contacts.name',
  'contacts.country',
  null,
  null,
  'contacts.transactions',
  'contacts.last',
  'contacts.actions',
] as const;
const current = (version: number) => active && generation === version;
function closeMenu(restore = true) {
  const trigger = menu.value?.trigger;
  menu.value = undefined;
  if (restore && trigger?.isConnected) trigger.focus({ preventScroll: true });
}
function outside(event: MouseEvent) {
  const target = event.target as Element;
  if (!menu.value || menuElement.value?.contains(target) || menu.value.trigger.contains(target))
    return;
  closeMenu(!target.closest('a, button, input, select, textarea, [tabindex], [contenteditable]'));
}
const closeForLayout = (event: Event) => {
  const opened = menu.value;
  if (!opened) return;
  const rect = opened.trigger.getBoundingClientRect();
  // A browser may deliver the scroll that brought the trigger into view after
  // its click. Only movement since opening invalidates this menu's placement.
  if (
    event.type === 'resize' ||
    Math.abs(rect.top - opened.top) > 0.5 ||
    Math.abs(rect.left - opened.left) > 0.5
  )
    closeMenu(false);
};
function menuKey(event: KeyboardEvent) {
  if (!menu.value) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeMenu();
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    menuElement.value?.querySelector('button')?.focus();
  } else if (event.key === 'Tab') closeMenu();
}
document.addEventListener('click', outside);
document.addEventListener('keydown', menuKey);
document.addEventListener('scroll', closeForLayout, true);
window.addEventListener('resize', closeForLayout);
onBeforeUnmount(() => {
  active = false;
  generation++;
  document.removeEventListener('click', outside);
  document.removeEventListener('keydown', menuKey);
  document.removeEventListener('scroll', closeForLayout, true);
  window.removeEventListener('resize', closeForLayout);
});
async function toggleMenu(row: Counterparty, event: MouseEvent) {
  const trigger = event.currentTarget as HTMLElement;
  if (menu.value?.trigger === trigger) {
    closeMenu();
    return;
  }
  const anchor = trigger.getBoundingClientRect();
  menu.value = { row, trigger, top: anchor.top, left: anchor.left };
  await nextTick();
  if (!menuElement.value || menu.value?.trigger !== trigger) return;
  const rect = trigger.getBoundingClientRect(),
    width = menuElement.value.offsetWidth,
    height = menuElement.value.offsetHeight;
  menuStyle.value = {
    left: `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`,
    top: `${Math.max(8, rect.bottom + height + 4 <= window.innerHeight - 8 ? rect.bottom + 4 : rect.top - height - 4)}px`,
  };
  menuElement.value.querySelector('button')?.focus({ preventScroll: true });
}
function openRename() {
  const selected = menu.value;
  closeMenu(false);
  if (selected) void rename.value?.open(selected.row, selected.trigger);
}
const navigate = (event: MouseEvent) => followSpaLink(event, props.context.services.navigate);
function rowClick(event: MouseEvent, row: Counterparty) {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    window.getSelection()?.toString() ||
    (event.target as Element).closest(
      'a, button, input, select, textarea, summary, [role=button], [contenteditable]',
    )
  )
    return;
  (event.currentTarget as HTMLElement).querySelector('a')?.focus({ preventScroll: true });
  props.context.services.navigate(contactUrl(row.counterparty_id));
}
async function load() {
  const version = ++generation,
    context = props.context;
  loading.value = true;
  error.value = undefined;
  party.value = undefined;
  rows.value = [];
  operations.value = [];
  nextOffset = 0;
  total.value = null;
  hasMore.value = false;
  operationBusy.value = false;
  history.value = undefined;
  historyError.value = false;
  historyOpen.value = false;
  historyRequest++;
  try {
    if (context.contactId) {
      const result = (await context.services.request(
        `/api/counterparties/${encodeURIComponent(context.contactId)}`,
      )) as { counterparty: Counterparty; periods: string[] };
      if (!current(version)) return;
      party.value = result.counterparty;
      periods.value = [...new Set([context.period, ...result.periods].filter(Boolean))];
      context.detailResolved(result.counterparty);
      void loadOperations(version);
    } else {
      const result = (await context.services.request('/api/counterparties')) as Counterparty[];
      if (!current(version)) return;
      rows.value = result;
    }
  } catch (failure) {
    if (current(version)) error.value = failure;
  } finally {
    if (current(version)) {
      loading.value = false;
      context.settled();
      await nextTick();
      if (current(version) && !context.contactId && !error.value) {
        context.restorePosition();
      }
    }
  }
}
watch(
  () => props.context,
  () => {
    closeMenu(false);
    void load();
  },
  { immediate: true },
);

async function loadOperations(version = generation) {
  const context = props.context;
  if (!context.contactId || operationBusy.value || !current(version)) return;
  operationBusy.value = true;
  operationError.value = false;
  try {
    const query = new URLSearchParams({ offset: String(nextOffset), limit: '50' });
    if (context.period) query.set('period', context.period);
    const page = (await context.services.request(
      `/api/counterparties/${encodeURIComponent(context.contactId)}/transactions?${query}`,
    )) as ContactPage;
    if (!current(version)) return;
    const ids = new Set(operations.value.map((row) => row.transaction_id));
    operations.value = [
      ...operations.value,
      ...page.rows.filter((row) => !ids.has(row.transaction_id)),
    ];
    nextOffset = page.next_offset;
    total.value = page.matching_count;
    hasMore.value = page.has_more;
  } catch {
    if (current(version)) operationError.value = true;
  } finally {
    if (current(version)) operationBusy.value = false;
  }
}
async function loadHistory() {
  const version = generation,
    request = ++historyRequest,
    id = props.context.contactId;
  if (!id) return;
  history.value = undefined;
  historyError.value = false;
  try {
    const result = (await props.context.services.request(
      `/api/counterparties/${encodeURIComponent(id)}/name-history`,
    )) as { changes: NameChange[] };
    if (current(version) && request === historyRequest) history.value = result.changes;
  } catch {
    if (current(version) && request === historyRequest) historyError.value = true;
  }
}
function toggleHistory(event: Event) {
  historyOpen.value = (event.target as HTMLDetailsElement).open;
  if (historyOpen.value && !history.value && !historyError.value) void loadHistory();
}
async function saved(result: Partial<Counterparty> & { changed: boolean }, id: string) {
  const version = generation,
    context = props.context;
  context.notify(message(result.changed ? 'contacts.nameSaved' : 'contacts.nameUnchanged'));
  if (party.value?.counterparty_id === id) {
    party.value = {
      ...party.value,
      ...result,
      ui_context: { ...party.value.ui_context, title: result.display_name },
    };
    context.detailResolved(party.value);
    history.value = undefined;
    historyRequest++;
    if (historyOpen.value) void loadHistory();
  } else {
    context.rememberPosition();
    rows.value = rows.value.map((row) =>
      row.counterparty_id === id
        ? { ...row, ...result, ui_context: { ...row.ui_context, title: result.display_name } }
        : row,
    );
    try {
      const fresh = (await context.services.request('/api/counterparties')) as Counterparty[];
      if (!current(version)) return;
      rows.value = fresh;
      chartRevision.value++;
      await nextTick();
      if (current(version)) {
        context.restorePosition();
      }
    } catch {
      if (current(version)) context.notify(message('contacts.nameRefreshError'), true);
    }
  }
  await nextTick();
  if (current(version))
    document
      .querySelector<HTMLElement>(`[data-vue-owned] [data-counterparty-menu="${id}"]`)
      ?.focus({ preventScroll: true });
}
const facts = computed(() => {
  if (!party.value) return [];
  const value = party.value,
    code = `parties.legalForms.${value.legal_form || 'unknown'}`;
  return [
    [t('contacts.country'), value.country_code === 'ZZ' ? null : value.country_code],
    ['NIF', value.tax_id],
    ['VAT ID', value.vat_id],
    [
      t('fields.legalForm'),
      messageIds.includes(code) ? formatMessage(code, {}, locale.value) : value.legal_form,
    ],
    [t('contacts.email'), value.email],
    [t('contacts.phone'), value.phone],
  ];
});
const notFound = computed(() => error.value instanceof ApiError && error.value.status === 404);
const forbidden = computed(
  () => error.value instanceof ApiError && error.value.code === 'session_forbidden',
);
const reload = () => window.location.reload();
defineExpose({
  canLeave: () => rename.value?.canLeave() ?? true,
  isDirty: () => rename.value?.isDirty() ?? false,
  isBusy: () => rename.value?.isBusy() ?? false,
});
</script>
<template>
  <div :aria-busy="loading" :class="context.contactId ? 'section-stack contact-detail' : ''">
    <a
      v-if="context.contactId"
      class="text-button contact-back-link"
      href="/contacts"
      @click="navigate"
      ><span aria-hidden="true">←</span> {{ t('contacts.back') }}</a
    >
    <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="empty-state state-error" role="alert">
      <p>
        {{
          t(
            notFound
              ? 'contacts.notFound'
              : forbidden
                ? 'common.sessionExpired'
                : 'common.loadFailed',
          )
        }}
      </p>
      <button v-if="!notFound" class="secondary-button" @click="forbidden ? reload() : load()">
        {{ t(forbidden ? 'common.reload' : 'common.retry') }}
      </button>
      <details v-if="!notFound">
        <summary>{{ t('issues.sourceDetails') }}</summary>
        <p>{{ errorMessage(error, locale) }}</p>
      </details>
    </div>
    <template v-else-if="!context.contactId">
      <div class="table-toolbar">
        <h2>{{ t('contacts.title') }}</h2>
      </div>
      <section class="panel">
        <div class="table-wrap" id="contacts-list-wrap">
          <table>
            <thead>
              <tr>
                <th
                  v-for="(key, index) in listColumns"
                  :key="index"
                  :class="index === 6 ? 'counterparty-action-cell' : ''"
                >
                  <template v-if="index === 3"
                    >ROI
                    <button
                      type="button"
                      class="term-help"
                      data-help-term="ROI"
                      :aria-label="`${t('fields.roiStatus')}`"
                      aria-describedby="accounting-tooltip"
                    >
                      ?
                    </button></template
                  ><span v-else-if="index === 6" class="sr-only">{{ t('contacts.actions') }}</span
                  ><template v-else>{{ key ? t(key) : 'NIF / VAT ID' }}</template>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in rows"
                :key="row.counterparty_id"
                :data-counterparty-row="row.counterparty_id"
                class="counterparty-row"
                @click="rowClick($event, row)"
              >
                <td class="cell-primary" :data-label="t('contacts.name')">
                  <a
                    class="counterparty-link"
                    :href="contactUrl(row.counterparty_id)"
                    @click="navigate"
                    ><strong>{{ row.display_name }}</strong></a
                  >
                </td>
                <td :data-label="t('contacts.country')">{{ row.country_code || '—' }}</td>
                <td data-label="NIF / VAT ID">{{ row.vat_id || row.tax_id || '—' }}</td>
                <td data-label="ROI"><StatusCell :context="row.ui_context" /></td>
                <td :data-label="t('contacts.transactions')">{{ row.transaction_count }}</td>
                <td :data-label="t('contacts.last')">
                  {{ formatDateText(row.last_transaction_on, locale) }}
                </td>
                <td class="counterparty-action-cell">
                  <button
                    type="button"
                    class="counterparty-more"
                    :data-counterparty-menu="row.counterparty_id"
                    :aria-label="t('contacts.actionsFor', { name: row.display_name })"
                    aria-haspopup="menu"
                    :aria-expanded="menu?.row.counterparty_id === row.counterparty_id"
                    aria-controls="vue-counterparty-actions-menu"
                    @click="toggleMenu(row, $event)"
                  >
                    <span aria-hidden="true">⋯</span>
                  </button>
                </td>
              </tr>
              <tr v-if="!rows.length">
                <td colspan="7">
                  <div class="empty-state">{{ t('common.noRecords') }}</div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <ChartHost
          id="chart-counterparty-concentration"
          kind="counterparty"
          :period="context.chartPeriod"
          :request="context.services.request"
          :revision="chartRevision"
        />
      </section>
    </template>
    <template v-else-if="party">
      <div id="contact-identity">
        <header class="contact-detail-header">
          <h2>{{ party.display_name }}</h2>
          <button
            type="button"
            class="counterparty-more"
            :data-counterparty-menu="party.counterparty_id"
            :aria-label="t('contacts.actionsFor', { name: party.display_name })"
            aria-haspopup="menu"
            :aria-expanded="!!menu"
            aria-controls="vue-counterparty-actions-menu"
            @click="toggleMenu(party, $event)"
          >
            <span aria-hidden="true">⋯</span>
          </button>
        </header>
        <section class="panel contact-facts-panel">
          <h3>{{ t('contacts.facts') }}</h3>
          <dl class="contact-facts">
            <div v-for="[label, value] in facts" :key="label || ''">
              <dt>{{ label }}</dt>
              <dd>{{ value || '—' }}</dd>
            </div>
            <div>
              <dt>{{ t('fields.roiStatus') }}</dt>
              <dd><StatusCell :context="party.ui_context" /></dd>
            </div>
          </dl>
        </section>
      </div>
      <section class="panel contact-operations">
        <header class="panel-header">
          <h3>{{ t('contacts.operations') }}</h3>
          <label class="contact-period-label" for="contact-period"
            >{{ t('toolbar.period')
            }}<select
              id="contact-period"
              :value="context.period"
              :aria-label="t('toolbar.period')"
              @change="
                context.services.navigate(
                  contactUrl(context.contactId!, ($event.target as HTMLSelectElement).value),
                )
              "
            >
              <option value="">{{ t('contacts.allPeriods') }}</option>
              <option v-for="period in periods" :key="period" :value="period">{{ period }}</option>
            </select></label
          >
        </header>
        <div id="contact-operations-results" class="contact-panel-body">
          <ContactOperations
            v-if="operations.length"
            :rows="operations"
            :contact-id="context.contactId"
            :period="context.period"
            :navigate="context.services.navigate"
          />
          <p v-if="operationError" role="alert">{{ t('contacts.operationsError') }}</p>
          <p v-else-if="operationBusy && !operations.length" role="status">
            {{ t('common.loading') }}
          </p>
          <p v-else-if="!operations.length">{{ t('contacts.noOperations') }}</p>
          <div class="contact-page-actions">
            <small v-if="total !== null">{{
              t('contacts.shown', { count: operations.length, total })
            }}</small
            ><button
              v-if="operationError || hasMore"
              id="contact-load-more"
              type="button"
              class="secondary-button"
              :disabled="operationBusy"
              @click="loadOperations()"
            >
              {{
                t(
                  operationBusy
                    ? 'common.loading'
                    : operationError
                      ? 'contacts.retry'
                      : 'contacts.more',
                )
              }}
            </button>
          </div>
        </div>
      </section>
      <details
        class="panel contact-history"
        id="contact-history"
        :open="historyOpen"
        @toggle="toggleHistory"
      >
        <summary>{{ t('contacts.nameHistory') }}</summary>
        <div id="contact-history-body" class="contact-panel-body">
          <NameHistory v-if="history" :changes="history" /><template v-else-if="historyError"
            ><p role="alert">{{ t('contacts.historyError') }}</p>
            <button type="button" class="secondary-button" @click="loadHistory">
              {{ t('contacts.retry') }}
            </button></template
          >
          <p v-else>{{ t('common.loading') }}</p>
        </div>
      </details>
    </template>
    <RenameDialog ref="rename" :services="context.services" @saved="saved" />
    <Teleport to="body"
      ><div
        v-if="menu"
        ref="menuElement"
        id="vue-counterparty-actions-menu"
        class="counterparty-actions-menu"
        role="menu"
        :aria-label="t('contacts.actions')"
        :style="menuStyle"
        data-vue-owned
      >
        <button type="button" role="menuitem" @click="openRename">
          {{ t('contacts.editName') }}
        </button>
      </div></Teleport
    >
  </div>
</template>
