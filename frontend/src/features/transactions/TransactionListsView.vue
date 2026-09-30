<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { eur } from '../../core/format.ts';
import { ApiError } from '../../core/http.ts';
import { errorMessage } from '../../core/error-message.ts';
import ChartHost from '../../charts/ChartHost.vue';
import TermHelp from '../../components/TermHelp.vue';
import ExpenseTable from './ExpenseTable.vue';
import IncomeTable from './IncomeTable.vue';
import { createExpensePager } from './expense-pager.ts';
import {
  copyable,
  listUrl,
  type TransactionListContext,
  type TransactionRow,
  type ExpensePage,
} from './model.ts';
const props = defineProps<{ context: TransactionListContext }>();
const { locale, t } = useLocale();
const query = ref(props.context.query),
  search = ref<HTMLInputElement>();
const page = shallowRef<ExpensePage>(),
  income = shallowRef<TransactionRow[]>([]);
const busy = ref(false),
  error = shallowRef<unknown>();
const kinds = ['purchase', 'amortization'] as const;
let active = true,
  generation = 0,
  timer: ReturnType<typeof setTimeout> | undefined;
let retryAppend = false,
  retryRefresh = false,
  pager: ReturnType<typeof createExpensePager>;
const expense = computed(() => props.context.kind === 'expense');
const sourceUrl = computed(() => listUrl(props.context.kind, props.context.period, query.value));
const current = (revision: number) => active && revision === generation;
async function load(append = false, refresh = false) {
  const revision = ++generation,
    context = props.context;
  busy.value = true;
  error.value = undefined;
  retryAppend = append;
  retryRefresh = refresh;
  try {
    if (context.kind === 'expense') {
      const result = await (refresh
        ? pager.refresh(query.value.trim())
        : pager.load(query.value.trim(), append));
      if (
        !result ||
        !current(revision) ||
        (refresh && document.querySelector('#status-help-dialog[open]'))
      )
        return;
      page.value = result;
    } else {
      const params = new URLSearchParams({ period: context.period, entry_type: 'income' });
      if (query.value.trim()) params.set('q', query.value.trim());
      const rows = (await context.services.request(
        `/api/transactions?${params}`,
      )) as TransactionRow[];
      if (!current(revision)) return;
      income.value = rows;
    }
  } catch (failure) {
    if (current(revision)) error.value = failure;
  } finally {
    if (current(revision)) {
      busy.value = false;
      context.settled();
    }
  }
}
watch(
  () => props.context,
  () => {
    generation++;
    clearTimeout(timer);
    pager?.invalidate();
    const context = props.context;
    pager = createExpensePager(
      async ({ query, offset }) =>
        (await context.services.request(
          `/api/expenses?${new URLSearchParams({ period: context.period, q: query, offset: String(offset) })}`,
        )) as ExpensePage,
    );
    query.value = context.query;
    page.value = undefined;
    income.value = [];
    void load();
  },
  { immediate: true },
);
function searchChanged() {
  generation++;
  pager.invalidate();
  clearTimeout(timer);
  props.context.queryChanged(query.value);
  error.value = undefined;
  if (expense.value) page.value = undefined;
  busy.value = true;
  timer = setTimeout(() => {
    void load();
  }, 240);
}
function refreshVisible() {
  if (
    expense.value &&
    !busy.value &&
    document.visibilityState === 'visible' &&
    document.activeElement !== search.value &&
    !document.querySelector('dialog[open]')
  )
    void load(false, true);
}
const interval = setInterval(refreshVisible, 60000);
document.addEventListener('visibilitychange', refreshVisible);
onBeforeUnmount(() => {
  active = false;
  generation++;
  pager.invalidate();
  clearTimeout(timer);
  clearInterval(interval);
  document.removeEventListener('visibilitychange', refreshVisible);
});

const forbidden = computed(
  () => error.value instanceof ApiError && error.value.code === 'session_forbidden',
);
const reload = () => window.location.reload();
function copy(row: TransactionRow) {
  if (income.value.includes(row) && copyable(row, props.context.copyTarget))
    props.context.copy(row);
}
</script>
<template>
  <div>
    <div class="table-toolbar" :class="{ 'expense-toolbar': expense }">
      <h2>{{ t(expense ? 'titles.expenses' : 'titles.income') }} · {{ context.period }}</h2>
      <div class="toolbar-filters">
        <label v-if="expense"
          ><span>{{ t('expense.search') }}</span
          ><input
            ref="search"
            id="expense-search"
            v-model="query"
            type="search"
            @input="searchChanged"
        /></label>
        <input
          v-else
          ref="search"
          id="transaction-search"
          v-model="query"
          type="search"
          :placeholder="t('transactions.search')"
          @input="searchChanged"
        />
        <button class="primary-button" id="view-add-entry" @click="context.openIntake">
          <span aria-hidden="true">+</span> {{ t('common.add') }}
        </button>
      </div>
    </div>
    <template v-if="expense">
      <div id="expense-results">
        <template v-if="page"
          ><section
            v-for="kind in kinds"
            :key="kind"
            class="panel expense-section"
            :aria-labelledby="`expenses-${kind}`"
          >
            <header class="expense-section-header">
              <h3 :id="`expenses-${kind}`">
                {{ t(kind === 'purchase' ? 'expense.purchase' : 'expense.amortization') }}
              </h3>
              <p>
                {{
                  t('expense.quarterTotal', {
                    amount: eur(page.summary[kind].reviewed_total.amount_eur, locale),
                  })
                }}
              </p>
              <p v-if="page.summary[kind].reviewed_total.missing_amount_count">
                {{ t('expense.missing') }}
              </p>
              <p v-if="kind === 'amortization'" class="expense-explanation">
                {{ t('expense.explanation') }}
              </p>
              <div class="expense-terms">
                <span>{{ t('transactions.status') }} <TermHelp term="posting" /></span
                ><span>IRPF <TermHelp term="IRPF" /></span
                ><span v-if="kind === 'purchase'">IVA <TermHelp term="IVA" /></span>
              </div>
              <small>{{
                t('expense.shown', {
                  shown: page.rows.filter((row) => row.expense_kind === kind).length,
                  count: page.matching_counts[kind],
                })
              }}</small>
            </header>
            <ExpenseTable
              v-if="page.rows.some((row) => row.expense_kind === kind)"
              :rows="page.rows.filter((row) => row.expense_kind === kind)"
              :kind="kind"
              :period="context.period"
              :return-url="sourceUrl"
              :navigate="context.services.navigate"
            />
            <div v-else class="empty-state">
              {{
                t(
                  page.matching_counts[kind]
                    ? 'expense.moreRecords'
                    : page.period_counts[kind]
                      ? 'expense.noMatches'
                      : 'expense.empty',
                )
              }}
            </div>
          </section></template
        >
      </div>
      <div id="expense-load-status" role="status" aria-live="polite">
        <template v-if="busy">{{ t('expense.loading') }}</template
        ><template v-else-if="error && !forbidden">{{ errorMessage(error, locale) }}</template>
      </div>
      <button
        v-if="page?.has_more"
        class="secondary-button"
        id="expense-more"
        :disabled="busy"
        @click="load(true)"
      >
        {{ t('expense.loadMore') }}
      </button>
      <button
        v-if="error && !forbidden"
        class="secondary-button"
        id="expense-retry"
        @click="load(retryAppend, retryRefresh)"
      >
        {{ t('expense.retry') }}
      </button>
      <section class="panel">
        <p class="expense-section-header">{{ t('expense.chartSourceNote') }}</p>
        <ChartHost
          id="chart-expense-structure"
          kind="expenseStructure"
          :period="context.period"
          :request="context.services.request"
        />
      </section>
    </template>
    <section v-else class="panel">
      <p v-if="busy && !income.length" role="status">{{ t('common.loading') }}</p>
      <div id="transactions-table">
        <IncomeTable
          v-if="!busy || income.length"
          :rows="income"
          :target="context.copyTarget"
          :query="query"
          :copy="copy"
        />
      </div>
    </section>
    <div v-if="error && (!expense || forbidden)" class="empty-state state-error" role="alert">
      <p>{{ t(forbidden ? 'common.sessionExpired' : 'common.loadFailed') }}</p>
      <button class="secondary-button" @click="forbidden ? reload() : load()">
        {{ t(forbidden ? 'common.reload' : 'common.retry') }}
      </button>
      <details>
        <summary>{{ t('issues.sourceDetails') }}</summary>
        <p>{{ errorMessage(error, locale) }}</p>
      </details>
    </div>
  </div>
</template>
