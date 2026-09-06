/* Read-only, domain-aware explanations. No posting or decision writes here. */
(() => {
  const words = AutonomoCore.legacy.helpWords;
  const statuses = AutonomoCore.refs.helpStatuses;
  // [title, explanation, next action]. Text is keyed by codes, never by messages.
  const reasons = AutonomoCore.refs.helpReasons;
  const terms = AutonomoCore.refs.helpTerms;
  const fields = AutonomoCore.refs.helpFields;
  const termNames = AutonomoCore.refs.helpTermNames;
  const registry = new Map();
  const scopedIds = new Set();
  let sequence = 0;
  let contextIds = new WeakMap();
  let currentLocale = "ru";
  let opener = null;
  let scrollY = 0;
  let panelUrl = "";
  let pendingNavigation = null;
  let historyAdapter = null;
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const lang = (key) => AutonomoCore.formatMessage(key, {}, currentLocale);
  const word = (key) => words[currentLocale][key] || key;
  const money = (v, currency = "EUR") =>
    v == null ||
    v === "" ||
    !Number.isFinite(Number(v)) ||
    !/^[A-Z]{3}$/.test(currency)
      ? word("missing")
      : new Intl.NumberFormat(AutonomoCore.localeTag(currentLocale), {
          style: "currency",
          currency,
        }).format(Number(v) / 100);
  function dateText(v) {
    const text = String(v || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return word("missing");
    const date = new Date(text + "T12:00:00");
    return Number.isNaN(date.getTime())
      ? word("missing")
      : new Intl.DateTimeFormat(
          AutonomoCore.localeTag(currentLocale),
        ).format(date);
  }
  function reasonKey(r) {
    return r.details?.issue_code || r.code || "unknown";
  }
  function reasonInfo(r) {
    return (
      reasons[r.code] ||
      reasons[reasonKey(r)] || [
        "help.inline.reasonNeedsClarification",
        "help.inline.noDetailedGuidanceExistsForThisReasonYetThe",
        "help.inline.openTheRelatedReviewAndShareTheSourceDetails",
      ]
    );
  }
  function allReasons(ctx) {
    const result = new Map();
    for (const r of [
      ...(ctx.reasons || []),
      ...(ctx.posting?.blockers || []),
    ]) {
      const k =
        (r.issue_id || r.details?.issue_id || reasonKey(r)) +
        ":" +
        (r.subject_id || "");
      if (!result.has(k)) result.set(k, r);
    }
    return [...result.values()].sort(
      (a, b) => Number(b.blocking !== false) - Number(a.blocking !== false),
    );
  }
  function label(ctx) {
    if (ctx.domain === "counterparty")
      return lang(
        {
          unknown: "help.inline.roiRegistrationUnverified",
          registered: "help.inline.roiRegistrationConfirmed",
          not_registered: "help.inline.notRegisteredInRoi",
        }[ctx.state] || statuses.unknown,
      );
    if (ctx.domain === "obligation" && ctx.state === "unknown")
      return lang(
        ctx.facts?.determination === "unknown"
          ? "help.inline.checkFilingObligation"
          : "help.inline.filingNotConfirmed",
      );
    const codes = new Set(
      (ctx.reasons || [])
        .filter((r) => r.blocking !== false)
        .map((r) => r.code),
    );
    if (
      ctx.domain === "asset" &&
      codes.has("advance_documents_overlap") &&
      codes.has("iva_prior_deduction_unconfirmed")
    )
      return lang("help.inline.awaitingAdvanceAndVatReview");
    return lang(statuses[ctx.state] || statuses.unknown);
  }
  function tone(ctx) {
    if (ctx.state === "blocked") return "attention";
    if ((ctx.reasons || []).some((r) => r.blocking !== false))
      return "attention";
    if (["ready", "posted", "filed"].includes(ctx.state)) return "positive";
    return ["needs_review", "warning"].includes(ctx.state)
      ? "pending"
      : "neutral";
  }
  function summary(ctx) {
    const first =
      ctx.domain === "issue"
        ? ctx.reasons?.[0]
        : allReasons(ctx).find((r) => r.blocking !== false);
    if (first) return lang(reasonInfo(first)[0]);
    if (ctx.posting?.preview_bucket === "deferred")
      return `${word("future")} ${dateText(ctx.posting.posting_deferred_until)}`;
    if (ctx.posting?.preview_bucket === "blocked" && ctx.state === "approved")
      return word("blocked");
    if (ctx.domain === "asset" && !ctx.facts?.advisor_decision)
      return word("noDecision");
    if (
      ctx.domain === "transaction" &&
      !["posted", "included_in_snapshot"].includes(ctx.state)
    )
      return word("valueProposal");
    if ((ctx.reasons || []).length) return word("advisory");
    return "";
  }
  function sweepRegistry() {
    const live = new Set([...document.querySelectorAll("[data-status-help]")]
      .map(button => button.dataset.statusHelp));
    if (dialog()?.open) live.add(window.history.state?.accountingHelp);
    for (const id of registry.keys()) if (!live.has(id) && !scopedIds.has(id)) registry.delete(id);
  }
  // Legacy renderers replace both roots and table fragments. Sweep after DOM
  // mutations settle so detached records are released without invalidating siblings.
  if (typeof MutationObserver !== "undefined" && document.body) {
    new MutationObserver(sweepRegistry).observe(document.body, {childList: true, subtree: true});
  }
  function register(ctx) {
    const existing = ctx && typeof ctx === "object" ? contextIds.get(ctx) : null;
    if (existing && registry.has(existing)) return existing;
    const id = String(++sequence);
    registry.set(id, ctx);
    if (ctx && typeof ctx === "object") contextIds.set(ctx, id);
    return id;
  }
  function createScope(context) {
    const id = String(++sequence);
    registry.set(id, context);
    scopedIds.add(id);
    let active = true;
    return {
      id,
      update(next) {if (active) registry.set(id, next);},
      dispose() {
        if (!active) return;
        active = false;
        scopedIds.delete(id);
        registry.delete(id);
        if (typeof window !== "undefined" && window.history.state?.accountingHelp === id) {
          dismiss();
          const state = {...window.history.state};
          delete state.accountingHelp;
          window.history.replaceState(state, "");
        }
      },
    };
  }
  function cell(ctx) {
    ctx = ctx || { domain: "unknown", state: "unknown" };
    const id = register(ctx);
    const subject = ctx.subject_id ? ` data-status-subject="${esc(`${ctx.domain || "unknown"}:${ctx.subject_id}`)}"` : "";
    return `<div class="status-context"><span class="badge status-${tone(ctx)}">${esc(label(ctx))}</span><small>${esc(summary(ctx))}</small><button type="button" class="text-button status-details-button" data-status-help="${id}"${subject} aria-haspopup="dialog">${esc(word("details"))}</button></div>`;
  }
  function term(key) {
    return `<button type="button" class="term-help" data-help-term="${esc(key)}" aria-label="${esc(word("help") + ": " + (termNames[key] ? lang(termNames[key]) : key))}" aria-describedby="accounting-tooltip">?</button>`;
  }
  function schedule(row) {
    const a = row.ui_context?.amortization;
    if (!a || !a.count) return `<span>${esc(word("none"))}</span>`;
    return `<div class="amortization-summary">${a.rows.some((r) => r.include_in_books) ? `<span>${esc(word("book"))}: <strong>${esc(money(a.book_minor))}</strong></span>` : ""}${a.rows.some((r) => !r.include_in_books) ? `<span>${esc(word("excluded"))}: <strong>${esc(money(a.excluded_minor))}</strong></span>` : ""}${a.rows.some((r) => r.entry_kind === "adjustment") ? `<small>${esc(word("adjustment"))}: ${esc(money(a.adjustment_minor))}</small>` : ""}</div>`;
  }
  function formatted(key, v, ctx) {
    if (v == null || v === "") return word("missing");
    if (key.endsWith("_minor")) return money(v, ctx.facts?.currency || "EUR");
    if (key === "business_use_ratio" || key === "annual_rate_basis_points")
      return new Intl.NumberFormat(AutonomoCore.localeTag(currentLocale), {
        style: "percent",
        maximumFractionDigits: 2,
      }).format(Number(v) / (key === "annual_rate_basis_points" ? 10000 : 1));
    if (/(_on|_at|_date)$/.test(key)) return dateText(v);
    if (key === "roi_status")
      return label({ domain: "counterparty", state: v });
    if (statuses[v]) return lang(statuses[v]);
    return String(v);
  }
  function content(ctx) {
    const rs = allReasons(ctx);
    const docs = ctx.documents || [];
    const facts = Object.entries(ctx.facts || {}).filter(
      ([key]) => fields[key],
    );
    const actions = (ctx.actions || []).filter(
      (a) =>
        a.kind === "review" && /^[\da-f-]{36}$/i.test(a.transaction_id || ""),
    );
    const am = ctx.amortization;
    return `<p class="status-panel-state badge status-${tone(ctx)}">${esc(label(ctx))}</p><p>${esc(summary(ctx))}</p>${
      rs.length
        ? `<section><h3>${esc(word("reasons"))}</h3>${rs
            .map((r, i) => {
              const info = reasonInfo(r);
              return `<article class="status-reason"><h4>${i + 1}. ${esc(lang(info[0]))}</h4><p>${esc(lang(info[1]))}</p><p><strong>${esc(word("next"))}:</strong> ${esc(lang(info[2]))}</p>${r.code === "advance_documents_overlap" ? `<p class="copy-question">${esc(lang("help.inline.whichAdvanceInvoicesForThisPurchaseRemainValidAnd"))}</p><button type="button" class="secondary-button" data-copy-question>${esc(word("copy"))}</button><p class="copy-feedback" role="status"></p>` : ""}<details><summary>${esc(word("technical"))}</summary><code>${esc(r.source_code || reasonKey(r))}</code><p>${esc(r.message || word("missing"))}</p></details></article>`;
            })
            .join("")}</section>`
        : ""
    }
      <section><h3>${esc(word("next"))}</h3>${actions.length ? actions.map((a) => `<a class="primary-button" data-help-review href="/review/${encodeURIComponent(a.transaction_id)}${a.period ? "?period=" + encodeURIComponent(a.period) : ""}">${esc(word("review"))}</a>`).join(" ") : `<p>${esc(word(["not_due", "filed", "registered"].includes(ctx.state) ? "noActionNeeded" : "noAction"))}</p>`}</section>
      <section><h3>${esc(word("known"))}</h3><dl class="status-facts">${facts.map(([key, v]) => `<div><dt>${esc(lang(fields[key]))}</dt><dd>${esc(formatted(key, v, ctx))}</dd></div>`).join("")}</dl></section>
      ${am ? `<section><h3>${esc(word("scope") + " " + (am.period || ""))}</h3>${schedule({ ui_context: ctx })}<p>${esc(word("sourceNote"))}</p><ul>${am.rows.map((r) => `<li>${esc(r.period_key)} · ${esc(money(r.amount_minor))} · ${esc(r.entry_kind === "adjustment" ? word("adjustment") : r.include_in_books ? word("book") : word("excluded"))}</li>`).join("")}</ul>${am.annual_evidence.length ? `<details><summary>${esc(word("annual"))}</summary><ul>${am.annual_evidence.map((r) => `<li>${esc(r.tax_year)} · ${esc(money(r.amount_minor))}</li>`).join("")}</ul></details>` : ""}</section>` : ""}
      ${docs.length ? `<section><h3>${esc(word("sources"))}</h3><ul class="status-sources">${docs.map((d) => `<li><strong>${esc(d.document_number || d.document_type)}</strong><span>${esc(dateText(d.issued_on))}${d.total_minor != null ? " · " + esc(money(d.total_minor, d.currency)) : ""}</span>${d.available ? `<a href="/api/document/${encodeURIComponent(d.document_id)}/content" target="_blank" rel="noreferrer">${esc(word("file"))}</a>` : `<span>${esc(word("missingFile"))}</span>`}</li>`).join("")}</ul></section>` : ""}
      ${ctx.source_message ? `<details><summary>${esc(word("original"))}</summary><p>${esc(ctx.source_message)}</p></details>` : ""}<p class="status-safety-note">${esc(word("safe"))}</p>`;
  }
  const dialog = () => document.getElementById("status-help-dialog");
  function dismiss() {
    const d = dialog();
    if (!d?.open) return;
    d.close();
    sweepRegistry();
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    window.scrollTo(0, scrollY);
    if (pendingNavigation) {
      const url = pendingNavigation;
      pendingNavigation = null;
      if (historyAdapter) historyAdapter.navigate(url);
      else window.location.assign(url);
    }
  }
  function open(id, trigger, push = true) {
    const ctx = registry.get(id);
    if (!ctx) return;
    hideTip();
    const d = dialog();
    opener = trigger || opener;
    scrollY = window.scrollY;
    panelUrl = window.location.href;
    document.getElementById("status-help-title").textContent =
      ctx.title || label(ctx);
    document.getElementById("status-help-body").innerHTML = content(ctx);
    document
      .getElementById("status-help-close")
      .setAttribute("aria-label", word("close"));
    if (!d.open) d.showModal();
    document.getElementById("status-help-close").focus();
    if (push && historyAdapter) historyAdapter.push(id);
    else if (push)
      window.history.pushState(
        { ...window.history.state, accountingHelp: id },
        "",
        panelUrl,
      );
  }
  function close() {
    if (window.history.state?.accountingHelp && dialog()?.open)
      historyAdapter ? historyAdapter.back() : window.history.back();
    else dismiss();
  }
  function handlePopState() {
    const key = window.history.state?.accountingHelp;
    if (key && registry.has(key) && window.location.href === panelUrl) {
      open(key, null, false);
      return true;
    }
    if (dialog()?.open) {
      dismiss();
      return window.location.href === panelUrl;
    }
    return false;
  }
  function beforeRender() {
    if (typeof document === "undefined") return;
    if (dialog()?.open) dismiss();
    if (window.history.state?.accountingHelp) {
      const s = { ...window.history.state };
      delete s.accountingHelp;
      window.history.replaceState(s, "");
    }
    registry.clear();
    scopedIds.clear();
    contextIds = new WeakMap();
  }
  let tipTrigger = null;
  let tipPinned = false;
  function hideTip() {
    const tip = document.getElementById("accounting-tooltip");
    if (tip) tip.hidden = true;
    tipTrigger = null;
    tipPinned = false;
  }
  function showTip(trigger) {
    const tip = document.getElementById("accounting-tooltip");
    const text = terms[trigger.dataset.helpTerm];
    if (!tip || !text) return;
    tipTrigger = trigger;
    tip.textContent = lang(text);
    tip.hidden = false;
    (trigger.closest("dialog") || document.body).appendChild(tip);
    const r = trigger.getBoundingClientRect();
    tip.style.left =
      Math.max(8, Math.min(r.left, window.innerWidth - 330)) + "px";
    tip.style.top =
      Math.min(r.bottom + 8, window.innerHeight - tip.offsetHeight - 8) + "px";
  }
  if (typeof document !== "undefined") {
    document.addEventListener("click", async (e) => {
      const trigger = e.target.closest?.("[data-status-help]");
      if (trigger) {
        open(trigger.dataset.statusHelp, trigger);
        return;
      }
      if (e.target.closest?.("#status-help-close")) {
        close();
        return;
      }
      const link = e.target.closest?.("[data-help-review]");
      if (link) {
        e.preventDefault();
        pendingNavigation = link.getAttribute("href");
        close();
        return;
      }
      const copy = e.target.closest?.("[data-copy-question]");
      if (copy) {
        const article = copy.closest("article");
        try {
          await navigator.clipboard.writeText(
            article.querySelector(".copy-question").textContent,
          );
          article.querySelector(".copy-feedback").textContent = word("copied");
        } catch {
          article.querySelector(".copy-feedback").textContent =
            word("copyError");
        }
        return;
      }
      const termTrigger = e.target.closest?.("[data-help-term]");
      if (termTrigger) {
        if (tipPinned && tipTrigger === termTrigger) hideTip();
        else {
          showTip(termTrigger);
          tipPinned = true;
        }
        return;
      }
      if (!e.target.closest?.("#accounting-tooltip")) hideTip();
    });
    document.addEventListener("focusin", (e) => {
      const el = e.target.closest?.("[data-help-term]");
      if (el) showTip(el);
    });
    document.addEventListener("focusout", () => {
      if (!tipPinned) hideTip();
    });
    document.addEventListener("mouseover", (e) => {
      const el = e.target.closest?.("[data-help-term]");
      if (el && !tipPinned) showTip(el);
    });
    document.addEventListener("mouseout", (e) => {
      if (
        !tipPinned &&
        (e.target.closest?.("[data-help-term]") ||
          e.target.closest?.("#accounting-tooltip")) &&
        !e.relatedTarget?.closest?.("[data-help-term],#accounting-tooltip")
      )
        hideTip();
    });
    document.addEventListener(
      "keydown",
      (e) => {
        if (e.key === "Escape" && tipTrigger && !dialog()?.open) {
          e.preventDefault();
          e.stopPropagation();
          hideTip();
        }
      },
      true,
    );
    document.addEventListener("DOMContentLoaded", () => {
      dialog()?.addEventListener("cancel", (e) => {
        e.preventDefault();
        close();
      });
    });
  }
  function labelTables(root) {
    for (const table of root.querySelectorAll("table")) {
      const labels = [...(table.tHead?.rows[0]?.cells || [])].map((h) =>
        h.textContent.replace(/\s*\?\s*/g, " ").trim(),
      );
      for (const body of table.tBodies)
        for (const row of body.rows)
          for (const [i, cell] of [...row.cells].entries())
            if (!cell.hasAttribute("data-label"))
              cell.dataset.label = labels[i] || "";
    }
  }
  globalThis.AccountingHelp = {
    setHistoryAdapter(adapter) {historyAdapter = adapter;},
    labelTables,
    createScope,
    setLocale: (l) => {
      currentLocale = AutonomoCore.normalizeLocale(l);
      const id = typeof window !== "undefined" ? window.history?.state?.accountingHelp : null;
      if (typeof document !== "undefined" && dialog()?.open && id && registry.has(id)) {
        const focus = AutonomoCore.captureFocus(dialog());
        const scroll = document.getElementById("status-help-body").scrollTop;
        document.getElementById("status-help-title").textContent = registry.get(id).title || label(registry.get(id));
        document.getElementById("status-help-body").innerHTML = content(registry.get(id));
        document.getElementById("status-help-body").scrollTop = scroll;
        AutonomoCore.restoreFocus(dialog(), focus);
      }
    },
    word,
    cell,
    term,
    schedule,
    label,
    summary,
    tone,
    money,
    reasonInfo,
    text: lang,
    termLabel: key => termNames[key] ? lang(termNames[key]) : key,
    allReasons,
    content,
    beforeRender,
    handlePopState,
  };
})();
