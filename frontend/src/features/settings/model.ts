import type { ViewServices } from '../../vue/services.ts';
export interface Activity {
  description: string;
  aeat_activity_code: string;
  aeat_activity_type: string;
  iae_group_epigraph: string;
  irpf_method: string;
  iva_regime: string;
  starts_on: string;
  ends_on?: string;
}
export interface Profile {
  taxpayer_profile_id: string | null;
  row_version: number;
  full_name: string;
  tax_id: string;
  residency_country: string;
  identity_locked?: boolean;
  tax_year_start_month?: number;
  activities?: Activity[];
}
export interface BackupRun {
  recorded_at: string;
  keep: number | null;
  offsite_status?: string;
  offsite?: boolean;
  settings_format?: number;
}
export interface Verification {
  recorded_at: string;
  status: string;
}
export interface Backups {
  available: boolean;
  revision: string;
  daily_keep: number | null;
  monthly_keep: number | null;
  last_success?: Partial<Record<'daily' | 'monthly', BackupRun>>;
  recovery_verification?: {
    monthly?: { last_attempt?: Verification; last_success?: Verification };
  };
}
export interface SettingsData {
  profiles: Profile[];
  backups: Backups;
}
export interface SettingsContext {
  services: ViewServices;
  settled(): void;
  onProfile(name: string): void;
  onLocale(locale: string): void;
  reload(): void;
}
export const emptyProfile = (): Profile => ({
  taxpayer_profile_id: null,
  row_version: 0,
  full_name: '',
  tax_id: '',
  residency_country: '',
  activities: [],
});
export const profileDraft = (profile: Profile) => ({
  full_name: profile.full_name,
  tax_id: profile.tax_id,
  residency_country: profile.residency_country,
});
export function retentionPayload(daily: string, monthly: string, revision: string) {
  return {
    expected_revision: revision,
    daily_keep: String(daily).trim() === '' ? null : Number(daily),
    monthly_keep: String(monthly).trim() === '' ? null : Number(monthly),
    confirm_local_pruning: true,
  };
}
