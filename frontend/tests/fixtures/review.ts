import type { WorkItem } from '../../src/features/review/guided-model.ts';
import {
  defaults,
  type WorkflowDraft,
  type ExpensePayload,
  type DeductionProposal,
  type DeductionRule,
} from '../../src/features/review/workflow-model.ts';
export const REVIEW_ID = '11111111-1111-4111-8111-111111111111';
export const DOCUMENT_ID = '22222222-2222-4222-8222-222222222222';
export function guidedFixture(): WorkItem {
  return {
    supported: true,
    review_allowed: true,
    posting_context: { preview_bucket: 'needs_review' },
    ui_context: { domain: 'transaction', state: 'needs_review', subject_id: REVIEW_ID },
    packet: {
      review_id: 'transaction:' + REVIEW_ID,
      snapshot_hash: 'synthetic-snapshot',
      state: {
        period: { period_key: '2026-Q3' },
        transaction: {
          transaction_id: REVIEW_ID,
          entry_type: 'income',
          transaction_date: '2026-07-02',
          currency: 'EUR',
        },
        counterparty: { country_code: 'ES' },
        issues: [],
      },
      decision: {
        business_purpose: '',
        reason: '',
        tax_treatment: { tax_code: 'domestic_output' },
        counterparty_changes: {},
        issue_resolutions: [],
      },
    },
  };
}
export function nativeFixture(): WorkflowDraft {
  const payload: ExpensePayload = {
    facts: {
      document_number: 'SYN-1',
      issued_on: '2026-07-02',
      transaction_date: '2026-07-02',
      booking_date: '2026-07-02',
      currency: 'EUR',
      gross_minor: 12100,
    },
    decision: {
      business_purpose: 'Synthetic purpose',
      reason: 'Synthetic reason',
      document_valid: true,
      tax_treatment: {
        tax_code: 'domestic_input',
        taxable_base_minor: 10000,
        vat_minor: 2100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 10000,
      },
    },
    asset: null,
    supplier: null,
    fx: null,
  };
  return {
    payload,
    source_values: JSON.parse(JSON.stringify(payload)),
    draft_version: 1,
    source_snapshot_hash: 'synthetic-source',
    current_snapshot_hash: 'synthetic-source',
    editable: true,
    conflict: false,
    source: {
      assets: [],
      document: { document_id: DOCUMENT_ID },
      period: { period_key: '2026-Q3' },
      issues: [],
    },
    activities: [],
    allowed_values: {
      tax_code: ['domestic_input', 'eu_service_expense'],
      aeat_invoice_type: ['F1'],
    },
  };
}

const NO_FACTS = {
  persons: null,
  persons_disabled: null,
  days: null,
  abroad: null,
  overnight: null,
  electronic_payment: null,
  evidence_confirmed: null,
};
const rule = (id: string, risk: string, evidence: string, facts: string[] = []): DeductionRule => ({
  id,
  risk,
  evidence,
  facts,
  sources: [],
});
/** Synthetic ready proposal for a 25% floor-area share of a EUR 100 + 21 IVA supplies invoice. */
export function utilityProposal(): DeductionProposal {
  return {
    rule_id: 'home_utility_partial_dwelling',
    status: 'ready',
    irpf_minor: 750,
    vat_minor: 525,
    suggested: { irpf_minor: 750, vat_minor: 525 },
    missing: [],
    explanation: [
      { code: 'vat_proportional_area', params: { share_basis_points: 2500 } },
      {
        code: 'irpf_coefficient_times_area',
        params: { coefficient_basis_points: 3000, share_basis_points: 2500 },
      },
    ],
    risk: 'medium',
    sources: [
      {
        title: 'Ley 35/2006 (IRPF), art. 30.2.5.ª b)',
        url: 'https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764#a30',
        checked_on: '2026-09-24',
        source_kind: 'primary',
      },
      {
        title: 'DGT, consulta vinculante V2554-23',
        url: 'https://example.invalid/synthetic-secondary-copy',
        checked_on: '2026-09-24',
        source_kind: 'secondary',
      },
    ],
    evidence: { required: 'factura_completa_nif', satisfied: true },
  };
}
/** A saved draft (browser defaults included) that intake pre-selected for the home supplies rule. */
export function ruleFixture(): WorkflowDraft {
  const draft = nativeFixture();
  const choice = { rule_id: 'home_utility_partial_dwelling', facts: { ...NO_FACTS } };
  draft.payload.deduction = choice;
  draft.source_values.deduction = { ...choice, facts: { ...NO_FACTS } };
  draft.payload.decision.tax_treatment.deductible_ratio = 0.25;
  draft.deduction_rules = [
    rule('general_business', 'low', 'factura_completa_nif'),
    rule('social_security_reta', 'low', 'bank_statement', ['evidence_confirmed']),
    rule('home_utility_partial_dwelling', 'medium', 'factura_completa_nif', ['area_share']),
    rule('home_rent_partial_dwelling', 'medium', 'bank_statement', [
      'area_share',
      'evidence_confirmed',
    ]),
    rule('health_insurance', 'medium', 'any', ['persons', 'persons_disabled']),
    rule('own_meals', 'high', 'factura_completa_nif', [
      'days',
      'abroad',
      'overnight',
      'electronic_payment',
    ]),
    rule('fine_or_surcharge', 'low', 'any'),
  ];
  draft.deduction_proposal = utilityProposal();
  defaults(draft.payload, draft.deduction_rules);
  return draft;
}
