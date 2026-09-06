<script setup lang="ts">
import { computed } from 'vue';
import { useShell } from './controller.ts';
import { routeUrl, views } from './routes.ts';
import { useLocale } from '../vue/locale.ts';
import { formatMessage, localeRegistry, messageText } from '../core/i18n.ts';
import { errorMessage } from '../core/error-message.ts';
import { followSpaLink } from '../vue/services.ts';
import IntakeDialog from '../features/intake/IntakeDialog.vue';
const {
  bootstrap,
  period,
  reviewTab,
  route,
  screen,
  view,
  intake,
  loading,
  failure,
  busy,
  resolved,
  revision,
  toast,
  locale,
  changeLocale,
  navigate,
  add,
  refresh,
  init,
  selectPeriod,
  intakeServices,
} = useShell();
const { t } = useLocale();
const icons = ['▦', '↗', '↙', '✓', '◇', '%', '◎', '⚙'];
const detail = computed(() => Boolean(route.value?.id));
const navView = computed(() =>
  route.value?.view === 'contact-detail'
    ? 'contacts'
    : route.value?.view === 'expense-detail'
      ? 'expenses'
      : route.value?.view,
);
const title = computed(() =>
  route.value?.view === 'contact-detail'
    ? t('contacts.cardTitle')
    : route.value?.view === 'expense-detail'
      ? t('expense.title')
      : formatMessage(`titles.${route.value?.view || 'dashboard'}`),
);
const refreshLabel = computed(() =>
  t(
    route.value?.view === 'contact-detail'
      ? 'contacts.refresh'
      : route.value?.view === 'expense-detail'
        ? 'expense.refresh'
        : 'toolbar.refresh',
  ),
);
</script>
<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">A</span
      ><span
        ><strong>Autónomo</strong><small>{{ t('brand.subtitle') }}</small></span
      >
    </div>
    <nav class="primary-nav" :aria-label="t('nav.aria')">
      <a
        v-for="(item, index) in views"
        :key="item"
        class="nav-item"
        :class="{ active: navView === item }"
        :href="routeUrl(item, period, { tab: reviewTab })"
        :data-view="item"
        data-spa
        @click="followSpaLink($event, navigate)"
        ><span aria-hidden="true">{{ icons[index] }}</span
        ><span :data-i18n="`nav.${item}`">{{ t(`nav.${item}`) }}</span></a
      >
    </nav>
    <a
      class="sidebar-footer storage-settings-link"
      href="/settings"
      data-spa
      @click="followSpaLink($event, navigate)"
      ><span aria-hidden="true">⚙</span><span>{{ t('storage.settings') }}</span></a
    >
  </aside>
  <main class="workspace">
    <header class="topbar">
      <div>
        <h1 id="page-title">{{ title }}</h1>
        <p id="profile-name">{{ bootstrap?.profile_name || 'Autónomo' }}</p>
      </div>
      <div class="topbar-actions">
        <div class="locale-control" role="group" :aria-label="t('locale.aria')">
          <button
            v-for="item in localeRegistry"
            :key="item.code"
            type="button"
            :data-locale="item.code"
            :class="{ active: locale === item.code }"
            :aria-pressed="locale === item.code"
            @click="changeLocale(item.code)"
          >
            {{ item.code.toUpperCase() }}
          </button>
        </div>
        <label
          class="period-control"
          :hidden="route?.view === 'contact-detail' || route?.view === 'settings'"
          ><span>{{ t('toolbar.period') }}</span
          ><select
            id="period-select"
            :aria-label="t('toolbar.periodAria')"
            :value="detail && !resolved ? '' : period"
            :disabled="detail"
            :title="detail ? t('toolbar.periodLocked') : ''"
            @change="selectPeriod(($event.target as HTMLSelectElement).value)"
          >
            <option
              v-for="item in bootstrap?.periods"
              :key="item.period_key"
              :value="item.period_key"
            >
              {{ item.period_key }}
            </option>
          </select></label
        >
        <button
          id="refresh-button"
          class="icon-button"
          :class="{ busy }"
          :title="refreshLabel"
          :aria-label="refreshLabel"
          :hidden="route?.view === 'settings'"
          :disabled="busy || (detail && route?.view !== 'contact-detail' && !resolved)"
          @click="refresh"
        >
          ↻
        </button>
        <button
          id="new-entry-button"
          class="primary-button"
          :hidden="detail || route?.view === 'settings'"
          :disabled="!bootstrap?.intake_enabled"
          :title="bootstrap?.intake_enabled ? '' : t('intake.disabledReason')"
          @click="add"
        >
          <span aria-hidden="true">+</span> <span>{{ t('common.add') }}</span>
        </button>
      </div>
    </header>
    <section id="app" class="content" data-vue-owned :aria-busy="loading">
      <div v-if="failure" class="empty-state" role="alert">
        <p>{{ errorMessage(failure, locale) }}</p>
        <button class="secondary-button" @click="init">{{ t('common.retry') }}</button>
      </div>
      <component
        v-else-if="screen"
        :is="screen.component"
        :key="revision"
        ref="view"
        :context="screen.context"
      />
      <div v-else-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
      <div v-else class="empty-state">
        <strong>{{ t('routes.unknownTitle') }}</strong>
        <p>{{ t('routes.unknownHint') }}</p>
        <a
          class="primary-button"
          href="/dashboard"
          data-spa
          @click="followSpaLink($event, navigate)"
          >{{ t('routes.backToDashboard') }}</a
        >
      </div>
    </section>
  </main>
  <IntakeDialog ref="intake" :services="intakeServices" />
  <dialog id="status-help-dialog" class="status-help-dialog" aria-labelledby="status-help-title">
    <header>
      <h2 id="status-help-title"></h2>
      <button id="status-help-close" type="button" :aria-label="t('common.close')">×</button>
    </header>
    <div id="status-help-body"></div>
  </dialog>
  <div id="accounting-tooltip" class="accounting-tooltip" role="tooltip" hidden></div>
  <div
    id="toast"
    class="toast"
    :class="{ visible: !!toast, error: toast?.error }"
    :hidden="!toast"
    role="status"
    aria-live="polite"
  >
    <span id="toast-message">{{ messageText(toast?.value, locale) }}</span
    ><button
      id="toast-close"
      class="toast-close"
      type="button"
      :aria-label="t('common.close')"
      @click="toast = undefined"
    >
      ×
    </button>
  </div>
</template>
