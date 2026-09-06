import { formatMessage, type Locale } from '../../core/i18n.ts';
import { eur as formatEur, formatDateText } from '../../core/format.ts';
import type { FormView, Obligation, TaxSummary } from './model.ts';
export function createFinancePresentation(locale: Locale) {
  const t = (key: string, params: Record<string, unknown> = {}) =>
    formatMessage(key, params, locale);
  const eur = (value: unknown) => formatEur(value, locale);
  const formatDate = (value: unknown) => formatDateText(value, locale);
  const formatMinorEur = (minor: number | null | undefined) => eur(Number(minor) / 100);
  function formCardData(
    form: FormView | undefined,
    obligation?: Obligation,
    { warnOnMissingHeadline = false } = {},
  ) {
    const fallback = form || {
      form_code: '',
      display_state: 'unavailable',
      filed_on: null,
      values: {},
      headline_value: null,
      headline_detail: null,
      preview_as_of: null,
      extraction_status: null,
    };
    const zeroSafeHeadline =
      fallback.headline_value !== null && fallback.headline_value !== undefined;
    const needsUnavailableWarning =
      warnOnMissingHeadline && !zeroSafeHeadline && fallback.display_state === 'filed';
    return {
      ...fallback,
      display_state: needsUnavailableWarning ? 'filed_without_values' : fallback.display_state,
      obligationFiled: obligation?.filing_status === 'filed',
    };
  }

  function formCardValue(form: FormView) {
    return form.headline_value !== null && form.headline_value !== undefined
      ? eur(form.headline_value)
      : '—';
  }

  function formAccentClass(form: FormView) {
    return ['filed', 'snapshot_only'].includes(form.display_state) ? '' : 'warning';
  }

  function formSubtitle(form: FormView, obligation?: Obligation) {
    const date = form.filed_on ? formatDate(form.filed_on) : null;
    const previewDate = form.preview_as_of ? formatDate(form.preview_as_of) : null;
    let stateText = '';
    if (form.display_state === 'filed') {
      stateText = date ? t('dashboard.filedOn', { date }) : t('dashboard.filed');
    } else if (form.display_state === 'filed_without_values') {
      const filedLabel = date ? t('dashboard.filedOn', { date }) : t('dashboard.filed');
      stateText = `${filedLabel} · ${t('dashboard.valuesUnavailable')}`;
    } else if (form.display_state === 'snapshot_only') {
      stateText = t('dashboard.snapshotAvailable');
    } else if (form.display_state === 'preview') {
      stateText = previewDate
        ? t('dashboard.calculatedAsOf', { date: previewDate })
        : t('dashboard.notCalculated');
    } else {
      stateText = t('dashboard.notCalculated');
    }
    if (
      obligation?.filing_status === 'filed' &&
      ['preview', 'unavailable'].includes(form.display_state)
    ) {
      stateText = `${t('dashboard.filed')} · ${stateText}`;
    }
    if (
      form.headline_detail !== null &&
      form.headline_detail !== undefined &&
      !['filed_without_values', 'unavailable'].includes(form.display_state)
    ) {
      return `${stateText} · ${t('dashboard.carryForward', { amount: eur(form.headline_detail) })}`;
    }
    return stateText;
  }

  function formEmptyState(form: FormView) {
    if (form.display_state === 'filed_without_values') {
      if (form.extraction_status === 'values_unavailable')
        return t('taxes.filedValuesUnavailableExtract');
      if (form.extraction_status === 'pdf_unreadable') return t('taxes.filedValuesUnavailablePdf');
      return t('taxes.filedValuesUnavailable');
    }
    return t('taxes.calculationMissing');
  }

  function taxSettlementLabel(status: string) {
    const keys: Record<string, string> = {
      planned: 'taxes.status.planned',
      unfiled: 'taxes.status.unfiled',
      payment_unconfirmed: 'taxes.status.paymentUnconfirmed',
      evidence_unavailable: 'taxes.status.evidenceUnavailable',
      partially_paid: 'taxes.status.partiallyPaid',
      paid: 'taxes.status.paid',
      overpaid: 'taxes.status.overpaid',
      no_payment_required: 'taxes.status.noPaymentRequired',
      undetermined: 'taxes.status.undetermined',
      not_started: 'taxes.status.notStarted',
    };
    return t(keys[status] || 'taxes.status.undetermined');
  }

  function taxStatusTone(status: string) {
    if (['paid', 'no_payment_required'].includes(status)) return 'positive';
    if (['overpaid', 'evidence_unavailable', 'undetermined'].includes(status)) return 'attention';
    if (['planned', 'unfiled', 'payment_unconfirmed', 'partially_paid'].includes(status))
      return 'pending';
    return 'neutral';
  }

  function taxHeadlineModel(
    periodState: { phase?: string } | undefined,
    summary: TaxSummary | undefined,
  ) {
    const status = summary?.settlement_status || 'undetermined';
    const phase = periodState?.phase || 'current';
    const payable = summary?.total_payable_minor;
    const paid = Number(summary?.total_confirmed_paid_minor || 0);
    const remaining = summary?.outstanding_minor;
    const overpaid = summary?.overpaid_minor;
    const calculated = summary?.calculated_as_of ? formatDate(summary.calculated_as_of) : null;

    if (status === 'not_started') {
      return {
        label: t('taxes.headlineNotStarted'),
        amount: null,
        detail: t('taxes.notStartedDetail'),
        tone: 'neutral',
      };
    }
    if (status === 'undetermined') {
      return {
        label: t('taxes.headlineUndetermined'),
        amount: null,
        detail: t('taxes.undeterminedDetail'),
        tone: 'attention',
      };
    }
    if (status === 'no_payment_required') {
      return {
        label: t('taxes.headlineNoPayment'),
        amount: 0,
        detail: t('taxes.noTaxPayment'),
        tone: 'positive',
      };
    }
    if (status === 'paid') {
      return {
        label: t('taxes.headlinePaid'),
        amount: paid,
        detail: t('taxes.paidDetail'),
        tone: 'positive',
      };
    }
    if (status === 'partially_paid') {
      return {
        label: t('taxes.headlineConfirmed'),
        amount: paid,
        detail: t('taxes.partialDetail', { amount: formatMinorEur(remaining) }),
        tone: 'pending',
      };
    }
    if (status === 'overpaid') {
      return {
        label: t('taxes.headlineConfirmed'),
        amount: paid,
        detail: t('taxes.overpaidDetail', { amount: formatMinorEur(overpaid) }),
        tone: 'attention',
      };
    }
    if (phase === 'current' && status === 'planned') {
      return {
        label: t('taxes.headlinePlanned'),
        amount: payable,
        detail: calculated
          ? t('taxes.currentDetail', { date: calculated })
          : t('taxes.currentDetailNoDate'),
        tone: 'accent',
      };
    }
    if (status === 'evidence_unavailable') {
      return {
        label: t('taxes.headlineConfirmed'),
        amount: paid,
        detail: t('taxes.evidenceUnavailableDetail'),
        tone: 'attention',
      };
    }
    if (status === 'unfiled') {
      return {
        label: t('taxes.headlineConfirmed'),
        amount: paid,
        detail: t('taxes.unfiledDetail'),
        tone: 'pending',
      };
    }
    return {
      label: t('taxes.headlineConfirmed'),
      amount: paid,
      detail: t(
        summary?.calculation_source === 'filed'
          ? 'taxes.unconfirmedDetail'
          : 'taxes.previewUnconfirmedDetail',
        { amount: payable == null ? '—' : formatMinorEur(payable) },
      ),
      tone: 'pending',
    };
  }

  return {
    formCardData,
    formCardValue,
    formAccentClass,
    formSubtitle,
    formEmptyState,
    taxSettlementLabel,
    taxStatusTone,
    taxHeadlineModel,
  };
}
