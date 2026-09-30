import type { ViewServices } from '../../vue/services.ts';
import { localeTag, type MessageId } from '../../core/i18n.ts';
import { eur, formatDateText } from '../../core/format.ts';
export type Scalar = string | number | boolean | null;
export type Fields = Record<string, Scalar>;
export interface ExpensePayload {
  facts: Fields;
  decision: {
    tax_treatment: Fields;
    business_purpose?: Scalar;
    reason?: Scalar;
    document_valid?: Scalar;
  };
  supplier: Fields | null;
  asset: Fields | null;
  fx: FxSelection | null;
  change_reason?: Scalar;
  manual_review_reason?: Scalar;
  deduction?: DeductionChoice | null;
}
export interface DeductionChoice {
  rule_id: string;
  facts: Fields;
}
export interface RuleSource {
  title: string;
  url: string;
  checked_on: string;
  source_kind: 'primary' | 'secondary';
}
export interface DeductionRule {
  id: string;
  risk: string;
  evidence: string;
  facts: string[];
  sources: RuleSource[];
}
export interface DeductionProposal {
  rule_id: string;
  status: 'ready' | 'needs_facts' | 'not_deductible' | 'out_of_scope' | 'unavailable';
  irpf_minor: number | null;
  vat_minor: number | null;
  suggested: { irpf_minor: number; vat_minor: number | null } | null;
  missing: string[];
  explanation: { code: string; params: Record<string, unknown> }[];
  risk?: string;
  sources?: RuleSource[];
  evidence?: { required: string; satisfied: boolean | null };
}
export interface FxSelection {
  rate_date?: Scalar;
  rate?: Scalar;
  rate_source: string;
  source_reference?: Scalar;
  raw_observation?: unknown;
  supersedes_rate_id: null;
}
export interface WorkflowDraft {
  payload: ExpensePayload;
  source_values: ExpensePayload;
  draft_version: number;
  source_snapshot_hash: string;
  current_snapshot_hash: string;
  editable: boolean;
  conflict: boolean;
  source: {
    assets: unknown[];
    document: { document_id: string };
    period: { period_key: string };
    issues: { issue_code: string }[];
  };
  activities: { business_activity_id: string; description: string }[];
  allowed_values: Record<string, string[]>;
  deduction_rules?: DeductionRule[];
  deduction_proposal?: DeductionProposal | null;
  fx_suggestion?: {
    status: string;
    note?: string;
    eur_per_unit?: string;
    rate_date?: string;
    source_reference?: string;
    raw_observation?: unknown;
  };
}
export interface WorkflowPreview {
  preview_token: string;
  supplier: string;
  period: string;
  currency: string;
  gross_minor: number;
  deductible_vat_minor: number;
  deductible_irpf_minor: number;
  future_depreciation_minor: number;
  schedule: { period_key: string; recognition_on?: string; amount_minor: number }[];
  deduction?: DeductionProposal | null;
}
export interface PostedExpense {
  transaction_id: string;
  follow_up_pending?: boolean;
}
export interface WorkflowContext {
  transactionId: string;
  services: ViewServices;
  onPosted(result: PostedExpense): void;
  onLegacy(): void;
  settled(): void;
}
export interface FieldSpec {
  path: string;
  label: MessageId;
  kind?: 'text' | 'money' | 'rate' | 'ratio' | 'boolean';
  type?: 'text' | 'date' | 'number';
}
export const factsFields: FieldSpec[] = [
  { path: 'facts.document_number', label: 'workflow.documentNumber' },
  { path: 'facts.issued_on', label: 'workflow.issueDate', type: 'date' },
  { path: 'facts.transaction_date', label: 'workflow.accountingDate', type: 'date' },
  { path: 'facts.booking_date', label: 'workflow.bookingDate', type: 'date' },
  { path: 'facts.currency', label: 'workflow.originalCurrency' },
  {
    path: 'facts.gross_minor',
    label: 'workflow.grossInOriginalCurrency',
    kind: 'money',
    type: 'number',
  },
];
export const taxFields: FieldSpec[] = [
  {
    path: 'decision.tax_treatment.taxable_base_minor',
    label: 'workflow.taxableBaseEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'decision.tax_treatment.vat_minor',
    label: 'workflow.invoiceIvaEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'decision.tax_treatment.deductible_vat_minor',
    label: 'workflow.deductibleIvaEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'decision.tax_treatment.deductible_irpf_minor',
    label: 'workflow.irpfDeductionEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'decision.tax_treatment.rate_basis_points',
    label: 'workflow.ivaRate',
    kind: 'rate',
    type: 'number',
  },
];
export const technicalFields: FieldSpec[] = [
  { path: 'decision.tax_treatment.aeat_operation_key', label: 'workflow.operationKey' },
  {
    path: 'decision.tax_treatment.aeat_operation_qualification',
    label: 'workflow.operationQualification',
  },
  { path: 'decision.tax_treatment.aeat_expense_concept', label: 'workflow.expenseConcept' },
  {
    path: 'decision.tax_treatment.withholding_minor',
    label: 'workflow.withholdingEur',
    kind: 'money',
    type: 'number',
  },
  {
    path: 'decision.tax_treatment.deductible_ratio',
    label: 'workflow.deductibleIvaShare',
    kind: 'ratio',
    type: 'number',
  },
];
export function fieldValue(payload: ExpensePayload, path: string): Scalar | undefined {
  let value: unknown = payload;
  for (const key of path.split('.'))
    value =
      value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined;
  return value as Scalar | undefined;
}
export function setField(payload: ExpensePayload, path: string, value: Scalar): void {
  const keys = path.split('.');
  if (keys.some((key) => ['__proto__', 'prototype', 'constructor'].includes(key)))
    throw new Error('Invalid field path');
  let target = payload as unknown as Record<string, unknown>;
  for (const key of keys.slice(0, -1)) {
    const child = target[key];
    if (!child || typeof child !== 'object') throw new Error('Missing field parent');
    target = child as Record<string, unknown>;
  }
  target[keys.at(-1)!] = value;
}
export const FACT_NAMES = [
  'persons',
  'persons_disabled',
  'days',
  'abroad',
  'overnight',
  'electronic_payment',
  'evidence_confirmed',
] as const;
const usesArea = (rules: DeductionRule[], id?: string | null) =>
  !!rules.find((rule) => rule.id === id)?.facts.includes('area_share');
/** A partly used home is never 100% business: an area rule starts without a share. */
export function chooseRule(payload: ExpensePayload, rules: DeductionRule[], id: string) {
  payload.deduction = id
    ? {
        rule_id: id,
        facts: Object.fromEntries(
          FACT_NAMES.map((name) => [name, payload.deduction?.facts[name] ?? null]),
        ),
      }
    : null;
  const tax = payload.decision.tax_treatment;
  if (usesArea(rules, id) && tax.deductible_ratio === 1) tax.deductible_ratio = null;
  // Leaving an area rule restores the same default share as defaults().
  if (!usesArea(rules, id) && tax.deductible_ratio == null) tax.deductible_ratio = 1;
}
/** Copies the backend's consistent IRPF/IVA pair; IVA stays manual when the rule sets none. */
export function copyProposal(payload: ExpensePayload, proposal: DeductionProposal): boolean {
  if (!proposal.suggested) return false;
  const tax = payload.decision.tax_treatment;
  tax.deductible_irpf_minor = proposal.suggested.irpf_minor;
  if (proposal.suggested.vat_minor != null) tax.deductible_vat_minor = proposal.suggested.vat_minor;
  return true;
}
/** The inputs a saved proposal was calculated from. */
export const proposalInputs = (payload: ExpensePayload) =>
  JSON.stringify([
    payload.facts,
    payload.decision.tax_treatment,
    payload.deduction ?? null,
    payload.asset,
    payload.fx,
  ]);
/** Server params are basis points and cents; messages take formatted percentages, money and dates. */
export function explanationValues(params: Record<string, unknown>, locale: unknown) {
  return Object.fromEntries(
    Object.entries(params).map(([key, value]) =>
      key.endsWith('_basis_points')
        ? [
            key.slice(0, -'_basis_points'.length),
            new Intl.NumberFormat(localeTag(locale), { maximumFractionDigits: 2 }).format(
              Number(value) / 100,
            ),
          ]
        : key.endsWith('_minor')
          ? [key.slice(0, -'_minor'.length), eur(Number(value) / 100, locale)]
          : /^\d{4}-\d{2}-\d{2}$/.test(String(value))
            ? [key, formatDateText(value, locale)]
            : [key, value ?? 'none'],
    ),
  );
}
export function defaults(payload: ExpensePayload, rules: DeductionRule[] = []) {
  for (const [key, value] of Object.entries({
    aeat_invoice_type: 'F1',
    aeat_operation_key: '01',
    aeat_operation_qualification: 'S1',
    aeat_reverse_charge: false,
    aeat_expense_concept: 'G03',
    withholding_minor: 0,
    include_modelo130: true,
    include_modelo303: true,
    include_modelo347: true,
    deductible_ratio: 1,
  }))
    if (
      payload.decision.tax_treatment[key] == null &&
      !(key === 'deductible_ratio' && usesArea(rules, payload.deduction?.rule_id))
    )
      payload.decision.tax_treatment[key] = value;
}
