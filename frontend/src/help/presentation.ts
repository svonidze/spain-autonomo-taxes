import { formatMessage, localeTag, type Locale } from '../core/i18n.ts';
import refs from './references.json' with { type: 'json' };
import type { HelpContext } from '../vue/help.ts';
export interface Reason {
  code?: string;
  details?: { issue_code?: string; issue_id?: string };
  issue_id?: string;
  subject_id?: string;
  blocking?: boolean;
  source_code?: string;
  message?: string;
}
export interface AccountingContext extends HelpContext {
  title?: string;
  reasons?: Reason[];
  facts?: Record<string, unknown>;
  posting?: { blockers?: Reason[]; preview_bucket?: string; posting_deferred_until?: string };
  documents?: {
    document_id: string;
    document_number?: string;
    document_type?: string;
    issued_on?: string;
    total_minor?: number | null;
    currency?: string;
    available?: boolean;
  }[];
  actions?: { kind: string; transaction_id: string; period?: string }[];
  source_message?: string;
  amortization?: {
    count: number;
    period?: string;
    book_minor?: number | null;
    excluded_minor?: number | null;
    adjustment_minor?: number | null;
    rows: {
      period_key?: string;
      amount_minor?: number;
      entry_kind?: string;
      include_in_books?: boolean;
    }[];
    annual_evidence: { tax_year?: number; amount_minor?: number }[];
  };
}
export function createPresentation(currentLocale: Locale) {
  const statuses: Record<string, string> = refs.Statuses,
    reasons: Record<string, string[]> = refs.Reasons,
    fields: Record<string, string> = refs.Fields,
    terms: Record<string, string> = refs.Terms,
    termNames: Record<string, string> = refs.TermNames;
  const esc = (v: unknown) =>
    String(v ?? '').replace(
      /[&<>"']/g,
      (c) =>
        ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[c] || c,
    );
  const lang = (key: string) => formatMessage(key, {}, currentLocale);
  const word = (key: string) => formatMessage(`help.words.${key}`, {}, currentLocale);
  const money = (v: unknown, currency = 'EUR') =>
    v == null || v === '' || !Number.isFinite(Number(v)) || !/^[A-Z]{3}$/.test(currency)
      ? word('missing')
      : new Intl.NumberFormat(localeTag(currentLocale), {
          style: 'currency',
          currency,
        }).format(Number(v) / 100);
  function dateText(v: unknown) {
    const text = String(v || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return word('missing');
    const date = new Date(text + 'T12:00:00');
    return Number.isNaN(date.getTime())
      ? word('missing')
      : new Intl.DateTimeFormat(localeTag(currentLocale)).format(date);
  }
  function reasonKey(r: Reason) {
    return r.details?.issue_code || r.code || 'unknown';
  }
  function reasonInfo(r: Reason) {
    return (
      reasons[r.code || ''] ||
      reasons[reasonKey(r)] || [
        'help.inline.reasonNeedsClarification',
        'help.inline.noDetailedGuidanceExistsForThisReasonYetThe',
        'help.inline.openTheRelatedReviewAndShareTheSourceDetails',
      ]
    );
  }
  function allReasons(ctx: AccountingContext) {
    const result = new Map<string, Reason>();
    for (const r of [...(ctx.reasons || []), ...(ctx.posting?.blockers || [])]) {
      const k = (r.issue_id || r.details?.issue_id || reasonKey(r)) + ':' + (r.subject_id || '');
      if (!result.has(k)) result.set(k, r);
    }
    return [...result.values()].sort(
      (a, b) => Number(b.blocking !== false) - Number(a.blocking !== false),
    );
  }
  function label(ctx: AccountingContext) {
    if (ctx.domain === 'counterparty')
      return lang(
        (
          {
            unknown: 'help.inline.roiRegistrationUnverified',
            registered: 'help.inline.roiRegistrationConfirmed',
            not_registered: 'help.inline.notRegisteredInRoi',
          } as Record<string, string>
        )[ctx.state] || statuses.unknown,
      );
    if (ctx.domain === 'obligation' && ctx.state === 'unknown')
      return lang(
        ctx.facts?.determination === 'unknown'
          ? 'help.inline.checkFilingObligation'
          : 'help.inline.filingNotConfirmed',
      );
    const codes = new Set(
      (ctx.reasons || []).filter((r) => r.blocking !== false).map((r) => r.code),
    );
    if (
      ctx.domain === 'asset' &&
      codes.has('advance_documents_overlap') &&
      codes.has('iva_prior_deduction_unconfirmed')
    )
      return lang('help.inline.awaitingAdvanceAndVatReview');
    return lang(statuses[ctx.state] || statuses.unknown);
  }
  function tone(ctx: AccountingContext) {
    if (ctx.state === 'blocked') return 'attention';
    if ((ctx.reasons || []).some((r) => r.blocking !== false)) return 'attention';
    if (['ready', 'posted', 'filed'].includes(ctx.state)) return 'positive';
    return ['needs_review', 'warning'].includes(ctx.state) ? 'pending' : 'neutral';
  }
  function summary(ctx: AccountingContext) {
    const first =
      ctx.domain === 'issue' ? ctx.reasons?.[0] : allReasons(ctx).find((r) => r.blocking !== false);
    if (first) return lang(reasonInfo(first)[0]);
    if (ctx.posting?.preview_bucket === 'deferred')
      return `${word('future')} ${dateText(ctx.posting.posting_deferred_until)}`;
    if (ctx.posting?.preview_bucket === 'blocked' && ctx.state === 'approved')
      return word('blocked');
    if (ctx.domain === 'asset' && !ctx.facts?.advisor_decision) return word('noDecision');
    if (ctx.domain === 'transaction' && !['posted', 'included_in_snapshot'].includes(ctx.state))
      return word('valueProposal');
    if ((ctx.reasons || []).length) return word('advisory');
    return '';
  }
  function schedule(row: { ui_context?: AccountingContext }) {
    const a = row.ui_context?.amortization;
    if (!a || !a.count) return `<span>${esc(word('none'))}</span>`;
    return `<div class="amortization-summary">${a.rows.some((r) => r.include_in_books) ? `<span>${esc(word('book'))}: <strong>${esc(money(a.book_minor))}</strong></span>` : ''}${a.rows.some((r) => !r.include_in_books) ? `<span>${esc(word('excluded'))}: <strong>${esc(money(a.excluded_minor))}</strong></span>` : ''}${a.rows.some((r) => r.entry_kind === 'adjustment') ? `<small>${esc(word('adjustment'))}: ${esc(money(a.adjustment_minor))}</small>` : ''}</div>`;
  }
  function formatted(key: string, v: unknown, ctx: AccountingContext) {
    if (v == null || v === '') return word('missing');
    if (key.endsWith('_minor')) return money(v, String(ctx.facts?.currency || 'EUR'));
    if (key === 'business_use_ratio' || key === 'annual_rate_basis_points')
      return new Intl.NumberFormat(localeTag(currentLocale), {
        style: 'percent',
        maximumFractionDigits: 2,
      }).format(Number(v) / (key === 'annual_rate_basis_points' ? 10000 : 1));
    if (/(_on|_at|_date)$/.test(key)) return dateText(v);
    if (key === 'roi_status') return label({ domain: 'counterparty', state: String(v) });
    if (statuses[String(v)]) return lang(statuses[String(v)]);
    return String(v);
  }
  function content(ctx: AccountingContext) {
    const rs = allReasons(ctx);
    const docs = ctx.documents || [];
    const facts = Object.entries(ctx.facts || {}).filter(([key]) => fields[key]);
    const actions = (ctx.actions || []).filter(
      (a) => a.kind === 'review' && /^[\da-f-]{36}$/i.test(a.transaction_id || ''),
    );
    const am = ctx.amortization;
    return `<p class="status-panel-state badge status-${tone(ctx)}">${esc(label(ctx))}</p><p>${esc(summary(ctx))}</p>${
      rs.length
        ? `<section><h3>${esc(word('reasons'))}</h3>${rs
            .map((r, i) => {
              const info = reasonInfo(r);
              return `<article class="status-reason"><h4>${i + 1}. ${esc(lang(info[0]))}</h4><p>${esc(lang(info[1]))}</p><p><strong>${esc(word('next'))}:</strong> ${esc(lang(info[2]))}</p>${r.code === 'advance_documents_overlap' ? `<p class="copy-question">${esc(lang('help.inline.whichAdvanceInvoicesForThisPurchaseRemainValidAnd'))}</p><button type="button" class="secondary-button" data-copy-question>${esc(word('copy'))}</button><p class="copy-feedback" role="status"></p>` : ''}<details><summary>${esc(word('technical'))}</summary><code>${esc(r.source_code || reasonKey(r))}</code><p>${esc(r.message || word('missing'))}</p></details></article>`;
            })
            .join('')}</section>`
        : ''
    }
      <section><h3>${esc(word('next'))}</h3>${actions.length ? actions.map((a) => `<a class="primary-button" data-help-review href="/review/${encodeURIComponent(a.transaction_id)}${a.period ? '?period=' + encodeURIComponent(a.period) : ''}">${esc(word('review'))}</a>`).join(' ') : `<p>${esc(word(['not_due', 'filed', 'registered'].includes(ctx.state) ? 'noActionNeeded' : 'noAction'))}</p>`}</section>
      <section><h3>${esc(word('known'))}</h3><dl class="status-facts">${facts.map(([key, v]) => `<div><dt>${esc(lang(fields[key]))}</dt><dd>${esc(formatted(key, v, ctx))}</dd></div>`).join('')}</dl></section>
      ${am ? `<section><h3>${esc(word('scope') + ' ' + (am.period || ''))}</h3>${schedule({ ui_context: ctx })}<p>${esc(word('sourceNote'))}</p><ul>${am.rows.map((r) => `<li>${esc(r.period_key)} · ${esc(money(r.amount_minor))} · ${esc(r.entry_kind === 'adjustment' ? word('adjustment') : r.include_in_books ? word('book') : word('excluded'))}</li>`).join('')}</ul>${am.annual_evidence.length ? `<details><summary>${esc(word('annual'))}</summary><ul>${am.annual_evidence.map((r) => `<li>${esc(r.tax_year)} · ${esc(money(r.amount_minor))}</li>`).join('')}</ul></details>` : ''}</section>` : ''}
      ${docs.length ? `<section><h3>${esc(word('sources'))}</h3><ul class="status-sources">${docs.map((d) => `<li><strong>${esc(d.document_number || d.document_type)}</strong><span>${esc(dateText(d.issued_on))}${d.total_minor != null ? ' · ' + esc(money(d.total_minor, d.currency)) : ''}</span>${d.available ? `<a href="/api/document/${encodeURIComponent(d.document_id)}/content" target="_blank" rel="noreferrer">${esc(word('file'))}</a>` : `<span>${esc(word('missingFile'))}</span>`}</li>`).join('')}</ul></section>` : ''}
      ${ctx.source_message ? `<details><summary>${esc(word('original'))}</summary><p>${esc(ctx.source_message)}</p></details>` : ''}<p class="status-safety-note">${esc(word('safe'))}</p>`;
  }
  return {
    label,
    tone,
    summary,
    word,
    money,
    reasonInfo,
    allReasons,
    content,
    text: lang,
    termText: (key: string) => (terms[key] ? lang(terms[key]) : ''),
    termLabel: (key: string) => (termNames[key] ? lang(termNames[key]) : key),
  };
}
