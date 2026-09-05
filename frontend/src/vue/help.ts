export interface HelpContext {domain: string; state: string; subject_id?: string; [key: string]: unknown;}
interface HelpScope {id: string; update(context: HelpContext): void; dispose(): void;}
export interface HelpAdapter {
  createScope(context: HelpContext): HelpScope;
  label(context: HelpContext): string;
  summary(context: HelpContext): string;
  tone(context: HelpContext): string;
  word(key: string): string;
}
// The shell owns dialog/history until stage 11. Cells own their individual records.
declare global {var AccountingHelp: HelpAdapter;}
export const helpAdapter = (): HelpAdapter => globalThis.AccountingHelp;
