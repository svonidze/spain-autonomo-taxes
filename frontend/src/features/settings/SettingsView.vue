<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef } from 'vue';
import { useLocale } from '../../vue/locale.ts';
import { message, messageText, localeRegistry, type UiMessage } from '../../core/i18n.ts';
import { ApiError } from '../../core/http.ts';
import { errorMessage } from '../../core/error-message.ts';
import BackupStatus from './BackupStatus.vue';
import {
  emptyProfile,
  profileDraft,
  retentionPayload,
  type SettingsContext,
  type SettingsData,
  type Profile,
  type Backups,
} from './model.ts';
const props = defineProps<{ context: SettingsContext }>();
const { locale, t } = useLocale();
const loading = ref(true),
  failure = shallowRef<unknown>(),
  profiles = ref<Profile[]>([]),
  profile = ref<Profile>(emptyProfile()),
  backups = ref<Backups>();
const draft = reactive(profileDraft(profile.value)),
  daily = ref(''),
  monthly = ref(''),
  pending = ref(false);
const profileFeedback = shallowRef<UiMessage>(),
  backupFeedback = shallowRef<UiMessage>(),
  profileError = ref(false),
  backupError = ref(false);
let active = true,
  baseline = '',
  backupBaseline = '';
onBeforeUnmount(() => {
  active = false;
});
const fingerprint = () => JSON.stringify(draft),
  backupFingerprint = () => JSON.stringify([daily.value, monthly.value]);
const isDirty = () =>
  !loading.value && (fingerprint() !== baseline || backupFingerprint() !== backupBaseline);
function canLeave() {
  if (pending.value) {
    profileFeedback.value = message('settings.form.busy');
    return false;
  }
  return !isDirty() || window.confirm(t('settings.form.leave'));
}
async function chooseLocale(event: Event) {
  props.context.onLocale((event.target as HTMLSelectElement).value);
  await nextTick();
  (event.target as HTMLSelectElement).value = locale.value;
}
function chooseProfile(event: Event) {
  const select = event.target as HTMLSelectElement;
  const next = profiles.value.find((row) => row.taxpayer_profile_id === select.value);
  if (pending.value || (fingerprint() !== baseline && !window.confirm(t('settings.form.leave')))) {
    select.value = profile.value.taxpayer_profile_id || '';
    return;
  }
  if (next) {
    profile.value = next;
    Object.assign(draft, profileDraft(next));
    baseline = fingerprint();
    profileFeedback.value = undefined;
  }
}
async function load() {
  loading.value = true;
  failure.value = undefined;
  try {
    const data = (await props.context.services.request('/api/settings')) as SettingsData;
    if (!active) return;
    profiles.value = data.profiles;
    profile.value = data.profiles[0] || emptyProfile();
    Object.assign(draft, profileDraft(profile.value));
    backups.value = data.backups;
    daily.value = String(data.backups.daily_keep ?? '');
    monthly.value = String(data.backups.monthly_keep ?? '');
    baseline = fingerprint();
    backupBaseline = backupFingerprint();
  } catch (error) {
    if (active) failure.value = error;
  } finally {
    if (active) {
      loading.value = false;
      props.context.settled();
    }
  }
}
async function submit(kind: 'profile' | 'backups') {
  if (pending.value || !backups.value) return;
  const payload =
    kind === 'profile'
      ? {
          ...draft,
          taxpayer_profile_id: profile.value.taxpayer_profile_id,
          expected_row_version: profile.value.row_version,
        }
      : retentionPayload(daily.value, monthly.value, backups.value.revision);
  if (kind === 'backups') {
    const limits = payload as ReturnType<typeof retentionPayload>;
    const limit = (count: number | null) =>
      count === null ? t('settings.form.unknownLimit') : t('settings.form.maximum', { count });
    if (
      !window.confirm(
        t('settings.form.confirmPruning', {
          daily: limit(limits.daily_keep),
          monthly: limit(limits.monthly_keep),
        }),
      )
    )
      return;
  }
  const feedback = kind === 'profile' ? profileFeedback : backupFeedback,
    errorFlag = kind === 'profile' ? profileError : backupError;
  pending.value = true;
  feedback.value = message('settings.form.saving');
  errorFlag.value = false;
  try {
    const result = await props.context.services.request(`/api/settings/${kind}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!active) return;
    if (kind === 'profile') {
      const updated = result as Profile;
      const index = profiles.value.findIndex(
        (row) => row.taxpayer_profile_id === updated.taxpayer_profile_id,
      );
      if (index < 0) profiles.value.push(updated);
      else profiles.value[index] = updated;
      profile.value = updated;
      Object.assign(draft, profileDraft(updated));
      baseline = fingerprint();
      feedback.value = message('settings.form.saved');
      props.context.onProfile(profiles.value[0].full_name);
    } else {
      backups.value = result as Backups;
      backupBaseline = backupFingerprint();
      feedback.value = message('settings.form.backupSaved');
    }
  } catch (error) {
    if (active) {
      errorFlag.value = true;
      feedback.value = message(
        error instanceof ApiError && error.code === 'identity_locked'
          ? 'settings.form.identityError'
          : error instanceof ApiError && error.code === 'duplicate_tax_id'
            ? 'settings.form.duplicate'
            : error instanceof ApiError && error.status === 409
              ? 'settings.form.conflict'
              : 'settings.form.error',
      );
    }
  } finally {
    if (active) pending.value = false;
  }
}
const activity = computed(() => profile.value.activities || []);
void load();
defineExpose({ canLeave, isDirty, isBusy: () => pending.value });
</script>
<template>
  <div v-if="loading" class="empty-state" role="status">{{ t('common.loading') }}</div>
  <div v-else-if="failure" class="empty-state" role="alert">
    <p>{{ errorMessage(failure, locale) }}</p>
    <button @click="load">{{ t('common.retry') }}</button>
  </div>
  <div v-else class="settings-page">
    <p class="settings-intro">{{ t('settings.form.intro') }}</p>
    <div class="settings-grid">
      <section class="settings-panel" aria-labelledby="settings-profile-title">
        <h2 id="settings-profile-title">{{ t('settings.form.profile') }}</h2>
        <div id="settings-profile-picker">
          <label v-if="profiles.length > 1"
            >{{ t('settings.form.select')
            }}<select
              id="settings-profile-select"
              :value="profile.taxpayer_profile_id"
              @change="chooseProfile"
            >
              <option
                v-for="row in profiles"
                :key="row.taxpayer_profile_id!"
                :value="row.taxpayer_profile_id!"
              >
                {{ row.full_name }} · {{ row.tax_id }}
              </option>
            </select></label
          >
          <p v-else-if="!profiles.length">{{ t('settings.form.empty') }}</p>
        </div>
        <form id="settings-profile-form" @submit.prevent="submit('profile')">
          <fieldset :disabled="pending">
            <label
              >{{ t('settings.form.name')
              }}<input
                v-model="draft.full_name"
                name="full_name"
                required
                maxlength="200"
                autocomplete="off" /></label
            ><label
              >{{ t('settings.form.tax')
              }}<input
                v-model="draft.tax_id"
                name="tax_id"
                required
                maxlength="64"
                autocomplete="off"
                :readonly="profile.identity_locked"
            /></label>
            <p v-if="profile.identity_locked" class="settings-hint">
              {{ t('settings.form.locked') }}
            </p>
            <label
              >{{ t('settings.form.country')
              }}<input
                v-model="draft.residency_country"
                name="residency_country"
                required
                maxlength="2"
                minlength="2"
                pattern="[A-Za-z]{2}"
                autocomplete="off"
                aria-describedby="settings-country-help"
            /></label>
            <p id="settings-country-help" class="settings-hint">
              {{ t('settings.form.countryHelp') }}
            </p>
            <p class="settings-hint">{{ t('settings.form.profileNote') }}</p>
            <button class="primary-button" type="submit">
              {{ t('settings.form.saveProfile') }}
            </button>
          </fieldset>
          <p
            class="settings-feedback"
            :class="{ 'settings-error': profileError }"
            :role="profileError ? 'alert' : 'status'"
            aria-live="polite"
          >
            {{ messageText(profileFeedback, locale) }}
          </p>
        </form>
      </section>
      <section class="settings-panel" aria-labelledby="settings-backup-title">
        <h2 id="settings-backup-title">{{ t('settings.form.backups') }}</h2>
        <template v-if="backups?.available"
          ><p>{{ t('settings.form.backupIntro') }}</p>
          <form id="settings-backup-form" @submit.prevent="submit('backups')">
            <fieldset :disabled="pending">
              <label
                >{{ t('settings.form.daily')
                }}<input
                  v-model="daily"
                  type="number"
                  name="daily_keep"
                  min="7"
                  max="365"
                  step="1"
                  :placeholder="t('settings.form.inherit')"
                  aria-describedby="settings-daily-help"
              /></label>
              <p id="settings-daily-help" class="settings-hint">
                {{ t('settings.form.dailyHelp') }}
              </p>
              <label
                >{{ t('settings.form.monthly')
                }}<input
                  v-model="monthly"
                  type="number"
                  name="monthly_keep"
                  min="3"
                  max="120"
                  step="1"
                  :placeholder="t('settings.form.inherit')"
                  aria-describedby="settings-monthly-help"
              /></label>
              <p id="settings-monthly-help" class="settings-hint">
                {{ t('settings.form.monthlyHelp') }}
              </p>
              <p class="settings-warning">{{ t('settings.form.backupNote') }}</p>
              <button class="primary-button" type="submit">
                {{ t('settings.form.saveBackups') }}
              </button>
            </fieldset>
            <p
              class="settings-feedback"
              :class="{ 'settings-error': backupError }"
              :role="backupError ? 'alert' : 'status'"
              aria-live="polite"
            >
              {{ messageText(backupFeedback, locale) }}
            </p>
          </form>
          <BackupStatus :backups="backups"
        /></template>
        <p v-else class="settings-warning" role="status">{{ t('settings.form.unavailable') }}</p>
        <p class="settings-hint">{{ t('settings.form.schedule') }}</p>
      </section>
      <section class="settings-panel" aria-labelledby="settings-activities-title">
        <h2 id="settings-activities-title">{{ t('settings.form.activities') }}</h2>
        <p class="settings-hint">{{ t('settings.form.activitiesNote') }}</p>
        <div id="settings-activities">
          <p class="settings-hint">
            {{ t('settings.form.yearStart') }}: {{ profile.tax_year_start_month || 1 }}
          </p>
          <article v-for="(row, index) in activity" :key="index" class="settings-activity">
            <h4>{{ row.description }}</h4>
            <dl>
              <dt>{{ t('settings.form.codes') }}</dt>
              <dd>
                {{ row.aeat_activity_code }} / {{ row.aeat_activity_type }} ·
                {{ row.iae_group_epigraph }}
              </dd>
              <dt>{{ t('settings.form.regimes') }}</dt>
              <dd>{{ row.irpf_method }} / {{ row.iva_regime }}</dd>
              <dt>{{ t('settings.form.dates') }}</dt>
              <dd>{{ row.starts_on }} — {{ row.ends_on || t('settings.form.current') }}</dd>
            </dl>
          </article>
          <p v-if="!activity.length" class="settings-hint">{{ t('settings.form.noActivities') }}</p>
        </div>
      </section>
      <section class="settings-panel" aria-labelledby="settings-interface-title">
        <h2 id="settings-interface-title">{{ t('settings.form.interface') }}</h2>
        <label
          >{{ t('settings.form.language')
          }}<select id="settings-locale" :value="locale" @change="chooseLocale">
            <option v-for="item in localeRegistry" :key="item.code" :value="item.code">
              {{ item.name }}
            </option>
          </select></label
        >
        <p class="settings-hint">{{ t('settings.form.languageNote') }}</p>
        <button id="settings-reload" class="secondary-button" type="button" @click="context.reload">
          {{ t('settings.form.reload') }}
        </button>
      </section>
    </div>
  </div>
</template>
