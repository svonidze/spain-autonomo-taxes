/* Account forms keep private drafts in memory only. */
(function (root) {
  "use strict";
  const copy = AutonomoCore.legacy.settingsCopy;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
  const translator = locale => (key, values = {}) => AutonomoCore.formatMessage(`settings.form.${key}`, values, locale);
  function retentionPayload(form, revision) {
    const count = name => form.elements.namedItem(name).value.trim() === "" ? null : Number(form.elements.namedItem(name).value);
    return {expected_revision: revision, daily_keep: count("daily_keep"), monthly_keep: count("monthly_keep"), confirm_local_pruning: true};
  }
  function pruningText(payload, locale) {
    const t = translator(locale);
    const limit = count => count === null ? t("unknownLimit") : t("maximum", {count});
    return t("confirmPruning", {daily: limit(payload.daily_keep), monthly: limit(payload.monthly_keep)});
  }
  function profileFields(profile, t) {
    return `<label>${t("name")}<input name="full_name" required maxlength="200" autocomplete="off" value="${escape(profile.full_name)}"></label>
      <label>${t("tax")}<input name="tax_id" required maxlength="64" autocomplete="off" value="${escape(profile.tax_id)}" ${profile.identity_locked ? "readonly" : ""}></label>
      ${profile.identity_locked ? `<p class="settings-hint">${t("locked")}</p>` : ""}
      <label>${t("country")}<input name="residency_country" required maxlength="2" minlength="2" pattern="[A-Za-z]{2}" autocomplete="off" value="${escape(profile.residency_country)}" aria-describedby="settings-country-help"></label>
      <p id="settings-country-help" class="settings-hint">${t("countryHelp")}</p>`;
  }
  function activityHtml(profile, t) {
    return `<p class="settings-hint">${t("yearStart")}: ${escape(profile.tax_year_start_month || 1)}</p>` + (profile.activities?.length
      ? profile.activities.map(activity => `<article class="settings-activity"><h4>${escape(activity.description)}</h4><dl>
        <dt>${t("codes")}</dt><dd>${escape(activity.aeat_activity_code)} / ${escape(activity.aeat_activity_type)} · ${escape(activity.iae_group_epigraph)}</dd>
        <dt>${t("regimes")}</dt><dd>${escape(activity.irpf_method)} / ${escape(activity.iva_regime)}</dd>
        <dt>${t("dates")}</dt><dd>${escape(activity.starts_on)} — ${escape(activity.ends_on || t("current"))}</dd></dl></article>`).join("")
      : `<p class="settings-hint">${t("noActivities")}</p>`);
  }
  function statusHtml(backups, locale) {
    const t = translator(locale);
    return ["daily", "monthly"].map(kind => {
      const run = backups.last_success?.[kind];
      let body = `<p>${t("unknown")}</p>`;
      if (run) {
        const time = new Date(run.recorded_at);
        const formatted = Number.isNaN(time.getTime()) ? "—" : new Intl.DateTimeFormat(AutonomoCore.localeTag(locale), {dateStyle: "medium", timeStyle: "short"}).format(time);
        body = `<p><time datetime="${escape(run.recorded_at)}">${escape(formatted)}</time></p>
          <p>${run.keep === null ? t("appliedUnknown") : t("applied", {count: escape(run.keep)})}</p>
          <p>${t(run.offsite_status === "acknowledged" ? "uploadAcknowledged" : run.offsite_status === "pending" ? "uploadPending" : run.offsite_status === "failed" ? "uploadFailed" : run.offsite_status === "disabled" ? "uploadDisabled" : run.offsite ? "offsiteYes" : "offsiteNo")}</p>`;
      }
      return `<article class="settings-run"><h4>${t(kind)}</h4>${body}</article>`;
    }).join("");
  }
  function verificationHtml(backups, locale) {
    const t = translator(locale);
    const verification = backups.recovery_verification?.monthly || {};
    const attempt = verification.last_attempt;
    const success = verification.last_success;
    const date = value => {
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? "—" : new Intl.DateTimeFormat(AutonomoCore.localeTag(locale), {dateStyle: "medium", timeStyle: "short"}).format(parsed);
    };
    if (!attempt) return `<p>${t("recoveryUnknown")}</p>`;
    const message = attempt.status === "success" ? t("recoverySuccess") : attempt.status === "running" ? t("recoveryRunning") : t("recoveryFailed");
    const previous = success && attempt.status !== "success" ? `<p>${t("recoveryPrevious", {date: escape(date(success.recorded_at))})}</p>` : "";
    return `<p>${message}</p><p><time datetime="${escape(attempt.recorded_at)}">${escape(date(attempt.recorded_at))}</time></p>${previous}`;
  }
  function mount(container, options) {
    const {data, locale, request, isCurrent, onProfile, onLocale, onReload, confirm} = options;
    const t = translator(locale);
    let active = true, pending = 0;
    let profiles = data.profiles;
    let profile = profiles[0] || {taxpayer_profile_id: null, row_version: 0, full_name: "", tax_id: "", residency_country: "", activities: []};
    let backups = data.backups;
    container.innerHTML = `<div class="settings-page"><p class="settings-intro">${t("intro")}</p><div class="settings-grid">
      <section class="settings-panel" aria-labelledby="settings-profile-title"><h2 id="settings-profile-title">${t("profile")}</h2>
        <div id="settings-profile-picker"></div><form id="settings-profile-form"><fieldset>${profileFields(profile, t)}
        <p class="settings-hint">${t("profileNote")}</p><button class="primary-button" type="submit">${t("saveProfile")}</button></fieldset>
        <p class="settings-feedback" role="status" aria-live="polite"></p></form></section>
      <section class="settings-panel" aria-labelledby="settings-backup-title"><h2 id="settings-backup-title">${t("backups")}</h2>
      ${backups.available ? `<p>${t("backupIntro")}</p><form id="settings-backup-form"><fieldset>
        <label>${t("daily")}<input type="number" name="daily_keep" min="7" max="365" step="1" value="${escape(backups.daily_keep)}" placeholder="${t("inherit")}" aria-describedby="settings-daily-help"></label>
        <p id="settings-daily-help" class="settings-hint">${t("dailyHelp")}</p>
        <label>${t("monthly")}<input type="number" name="monthly_keep" min="3" max="120" step="1" value="${escape(backups.monthly_keep)}" placeholder="${t("inherit")}" aria-describedby="settings-monthly-help"></label>
        <p id="settings-monthly-help" class="settings-hint">${t("monthlyHelp")}</p>
        <p class="settings-warning">${t("backupNote")}</p><button class="primary-button" type="submit">${t("saveBackups")}</button></fieldset>
        <p class="settings-feedback" role="status" aria-live="polite"></p></form>
        <h3>${t("lastSuccess")}</h3><div class="settings-runs">${statusHtml(backups, locale)}</div>
        <h3>${t("recoveryCheck")}</h3><div class="settings-runs">${verificationHtml(backups, locale)}</div>
        ${!Object.values(backups.last_success || {}).some(run => run?.settings_format === 1) ? `<p class="settings-hint">${t("consumerUnknown")}</p>` : ""}` : `<p class="settings-warning" role="status">${t("unavailable")}</p>`}
        <p class="settings-hint">${t("schedule")}</p></section>
      <section class="settings-panel" aria-labelledby="settings-activities-title"><h2 id="settings-activities-title">${t("activities")}</h2><p class="settings-hint">${t("activitiesNote")}</p><div id="settings-activities">${activityHtml(profile, t)}</div></section>
      <section class="settings-panel" aria-labelledby="settings-interface-title"><h2 id="settings-interface-title">${t("interface")}</h2>
        <label>${t("language")}<select id="settings-locale">${AutonomoCore.localeRegistry.map(item => `<option value="${escape(item.code)}" ${locale === item.code ? "selected" : ""}>${escape(item.name)}</option>`).join("")}</select></label><p class="settings-hint">${t("languageNote")}</p>
        <button id="settings-reload" class="secondary-button" type="button">${t("reload")}</button></section>
      </div></div>`;
    const profileForm = container.querySelector("#settings-profile-form");
    const backupForm = container.querySelector("#settings-backup-form");
    const field = (form, name) => form.elements.namedItem(name);
    const profileDraft = () => Object.fromEntries(["full_name", "tax_id", "residency_country"].map(name => [name, field(profileForm, name).value]));
    const backupDraft = () => backupForm ? [field(backupForm, "daily_keep").value, field(backupForm, "monthly_keep").value] : [];
    let profileBaseline = JSON.stringify(profileDraft()), backupBaseline = JSON.stringify(backupDraft());
    const isDirty = () => active && (JSON.stringify(profileDraft()) !== profileBaseline || JSON.stringify(backupDraft()) !== backupBaseline);
    const alive = () => active && isCurrent();
    const feedback = (form, message, error = false) => {
      const node = form.querySelector(".settings-feedback");
      node.textContent = message;
      node.classList.toggle("settings-error", error);
      node.setAttribute("role", error ? "alert" : "status");
    };
    function renderPicker() {
      const host = container.querySelector("#settings-profile-picker");
      host.innerHTML = profiles.length > 1 ? `<label>${t("select")}<select id="settings-profile-select">${profiles.map(row => `<option value="${escape(row.taxpayer_profile_id)}" ${row.taxpayer_profile_id === profile.taxpayer_profile_id ? "selected" : ""}>${escape(row.full_name)} · ${escape(row.tax_id)}</option>`).join("")}</select></label>` : profiles.length ? "" : `<p>${t("empty")}</p>`;
      host.querySelector("select")?.addEventListener("change", event => {
        if (pending || (JSON.stringify(profileDraft()) !== profileBaseline && !confirm(t("leave")))) {
          event.target.value = profile.taxpayer_profile_id;
          return;
        }
        profile = profiles.find(row => row.taxpayer_profile_id === event.target.value);
        const section = profileForm.querySelector("fieldset");
        section.innerHTML = `${profileFields(profile, t)}<p class="settings-hint">${t("profileNote")}</p><button class="primary-button" type="submit">${t("saveProfile")}</button>`;
        profileBaseline = JSON.stringify(profileDraft());
        feedback(profileForm, "");
        container.querySelector("#settings-activities").innerHTML = activityHtml(profile, t);
      });
    }
    async function submit(form, url, payload, done) {
      if (pending) return;
      pending++;
      const fieldset = form.querySelector("fieldset");
      fieldset.disabled = true;
      feedback(form, t("saving"));
      try {
        const result = await request(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
        if (alive()) done(result);
      } catch (error) {
        if (alive()) feedback(form, t(error.code === "identity_locked" ? "identityError" : error.code === "duplicate_tax_id" ? "duplicate" : error.status === 409 ? "conflict" : "error"), true);
      } finally {
        pending--;
        if (alive()) fieldset.disabled = false;
      }
    }
    renderPicker();
    profileForm.addEventListener("submit", event => {
      event.preventDefault();
      if (pending) return;
      return submit(profileForm, "/api/settings/profile", {...profileDraft(), taxpayer_profile_id: profile.taxpayer_profile_id, expected_row_version: profile.row_version}, result => {
        const index = profiles.findIndex(row => row.taxpayer_profile_id === result.taxpayer_profile_id);
        if (index < 0) profiles.push(result); else profiles[index] = result;
        profile = result;
        for (const name of ["full_name", "tax_id", "residency_country"]) field(profileForm, name).value = result[name];
        field(profileForm, "tax_id").readOnly = result.identity_locked;
        profileBaseline = JSON.stringify(profileDraft());
        feedback(profileForm, t("saved"));
        onProfile(profiles[0].full_name);
        renderPicker();
      });
    });
    backupForm?.addEventListener("submit", event => {
      event.preventDefault();
      if (pending) return;
      const payload = retentionPayload(backupForm, backups.revision);
      if (!confirm(pruningText(payload, locale))) return;
      return submit(backupForm, "/api/settings/backups", payload, result => {
        backups = result;
        backupBaseline = JSON.stringify(backupDraft());
        feedback(backupForm, t("backupSaved"));
      });
    });
    container.querySelector("#settings-reload").addEventListener("click", onReload);
    container.querySelector("#settings-locale").addEventListener("change", async event => {
      await onLocale(event.target.value);
      if (alive()) event.target.value = locale;
    });
    return {
      isDirty, isBusy: () => pending > 0,
      canLeave() {
        if (pending) { feedback(profileForm, t("busy")); return false; }
        return !isDirty() || confirm(t("leave"));
      },
      dispose() { active = false; },
    };
  }
  const api = {mount, retentionPayload, pruningText, statusHtml, verificationHtml, profileFields, translator, copy};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  // Bundlers provide a CommonJS module too; the legacy shell still uses this API.
  root.AutonomoSettings = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
