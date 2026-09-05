import type {WorkItem} from '../../src/features/review/guided-model.ts';
import type {WorkflowDraft,ExpensePayload} from '../../src/features/review/workflow-model.ts';
export const REVIEW_ID='11111111-1111-4111-8111-111111111111';
export const DOCUMENT_ID='22222222-2222-4222-8222-222222222222';
export function guidedFixture(): WorkItem {
  return {supported:true,review_allowed:true,posting_context:{preview_bucket:'needs_review'},ui_context:{domain:'transaction',state:'needs_review',subject_id:REVIEW_ID},
    packet:{review_id:'transaction:'+REVIEW_ID,snapshot_hash:'synthetic-snapshot',state:{period:{period_key:'2026-Q3'},transaction:{transaction_id:REVIEW_ID,entry_type:'income',transaction_date:'2026-07-02',currency:'EUR'},counterparty:{country_code:'ES'},issues:[]},decision:{business_purpose:'',reason:'',tax_treatment:{tax_code:'domestic_output'},counterparty_changes:{},issue_resolutions:[]}}};
}
export function nativeFixture(): WorkflowDraft {
  const payload: ExpensePayload={facts:{document_number:'SYN-1',issued_on:'2026-07-02',transaction_date:'2026-07-02',booking_date:'2026-07-02',currency:'EUR',gross_minor:12100},decision:{business_purpose:'Synthetic purpose',reason:'Synthetic reason',document_valid:true,tax_treatment:{tax_code:'domestic_input',taxable_base_minor:10000,vat_minor:2100,deductible_vat_minor:2100,deductible_irpf_minor:10000}},asset:null,supplier:null,fx:null};
  return {payload,source_values:JSON.parse(JSON.stringify(payload)),draft_version:1,source_snapshot_hash:'synthetic-source',current_snapshot_hash:'synthetic-source',editable:true,conflict:false,source:{assets:[],document:{document_id:DOCUMENT_ID},period:{period_key:'2026-Q3'},issues:[]},activities:[],allowed_values:{tax_code:['domestic_input','eu_service_expense'],aeat_invoice_type:['F1']}};
}
