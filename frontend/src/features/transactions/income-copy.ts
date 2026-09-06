import { message, type UiMessage } from '../../core/i18n.ts';
import type { TransactionRow } from './model.ts';
export interface CopyContext {
  sourcePeriodKey: string;
  targetPeriodKey: string;
  targetIsCurrentQuarter: boolean;
  currencyOptions: string[];
  today?: Date;
}
export function buildIncomeCopy(row: TransactionRow, context: CopyContext) {
  const today = context.today || new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const candidate =
    context.sourcePeriodKey === context.targetPeriodKey
      ? String(row.transaction_date || '')
      : context.targetIsCurrentQuarter
        ? localDate
        : '';
  const match = /^(\d{4})-(\d{2})-\d{2}$/.exec(candidate),
    month = match ? Number(match[2]) : 0;
  const quarter =
    match && month >= 1 && month <= 12 ? `${match[1]}-Q${Math.floor((month - 1) / 3) + 1}` : '';
  const prefill = {
    issued_on: quarter === context.targetPeriodKey ? candidate : '',
    counterparty_name: String(row.counterparty_name || '').trim(),
    document_number: String(row.document_number || '').trim(),
    currency: '',
    gross: '',
  };
  const noticeLines: UiMessage[] = [
    message('intake.copyNotice', { period: context.targetPeriodKey }),
  ];
  const currency = String(row.currency || '')
      .trim()
      .toUpperCase(),
    amount = row.amount_original == null ? '' : String(row.amount_original).trim();
  const supported = amount !== '' && Number.isFinite(Number(amount)) && Number(amount) >= 0;
  if (currency && supported && context.currencyOptions.includes(currency)) {
    prefill.currency = currency;
    prefill.gross = amount;
  }
  if (currency && !context.currencyOptions.includes(currency))
    noticeLines.push(message('intake.copyUnsupportedCurrency', { currency }));
  if (amount !== '' && !supported) noticeLines.push(message('intake.copyInvalidAmount'));
  return { prefill, noticeLines };
}
