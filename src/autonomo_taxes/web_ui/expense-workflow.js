/* Expense-only UI. Accounting decisions are validated and committed by the server. */
(function (root) {
  "use strict";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const money = (value) => value == null ? "—" : (Number(value) / 100).toFixed(2);
  const set = (object, path, value) => {
    const keys = path.split(".");
    const last = keys.pop();
    keys.reduce((target, key) => target[key], object)[last] = value;
  };
  function read(input) {
    if (input.type === "checkbox") return input.checked;
    if (input.value === "") return input.dataset.kind === "text" ? "" : null;
    if (input.dataset.kind === "money") return Math.round(Number(input.value) * 100);
    if (input.dataset.kind === "rate") return Math.round(Number(input.value) * 100);
    if (input.dataset.kind === "ratio") return Number(input.value) / 100;
    if (input.dataset.kind === "boolean") return input.value === "true";
    return input.value;
  }
  function pendingRequest(key, token, version) {
    let saved;
    try { saved = JSON.parse(root.sessionStorage.getItem(key)); } catch (_) { /* private storage may be disabled */ }
    if (saved?.preview_token === token && saved?.expected_version === version) return saved;
    const request = {preview_token: token, expected_version: version, request_id: root.crypto.randomUUID()};
    try { root.sessionStorage.setItem(key, JSON.stringify(request)); } catch (_) { /* same-page retry retains the key */ }
    return request;
  }

  async function open(options) {
    const {container, api, transactionId, onPosted} = options;
    let locale = options.locale || AutonomoCore.defaultLocale;
    let active = true;
    const isActive = () => active && options.isActive();
    const ui = {supplierSearch: "", settlementRate: "", settlementReference: "", technicalOpen: false};
    const tr = (key, values = {}) => AutonomoCore.formatMessage(key, values, locale);
    const base = `/api/expense-workflows/${encodeURIComponent(transactionId)}`;
    let [draft, suppliers] = await Promise.all([api(base), api("/api/counterparties")]);
    if (!isActive()) return;
    if (draft.source.assets.length && options.onLegacy) {await options.onLegacy();return;}
    let payload = copy(draft.payload), proposed = null, request = null, error = "", message = "", posted = null, busy = false;
    const tax = () => payload.decision.tax_treatment;
    const field = (path, label, value, kind = "text", type = "text") => {
      const display = value == null ? "" : kind === "money" || kind === "rate" ? Number(value)/100 : kind === "ratio" ? Number(value)*100 : value;
      return `<label><span>${escape(label)}</span><input data-p="${escape(path)}" data-kind="${kind}" type="${type}" ${type === "number" ? 'step="0.01"' : ""} value="${escape(display)}"></label>`;
    };
    const select = (path, label, value, items, kind = "text") => `<label><span>${escape(label)}</span><select data-p="${path}" data-kind="${kind}"><option value=""></option>${items.map(([v,l]) => `<option value="${escape(v)}"${String(value) === String(v) ? " selected" : ""}>${escape(l)}</option>`).join("")}</select></label>`;
    const checkbox = (path, label, value) => `<label class="wf-check"><input type="checkbox" data-p="${path}" ${value === true ? "checked" : ""}><span>${escape(label)}</span></label>`;
    const sourceUrl = `/api/document/${encodeURIComponent(draft.source.document.document_id)}/content`;
    function defaults() {
      const t = tax();
      for (const [key,value] of Object.entries({aeat_invoice_type:"F1",aeat_operation_key:"01",aeat_operation_qualification:"S1",aeat_reverse_charge:false,aeat_expense_concept:"G03",withholding_minor:0,include_modelo130:true,include_modelo303:true,include_modelo347:true,deductible_ratio:1})) {
        if (t[key] == null) t[key] = value;
      }
    }
    defaults();
    function render() {
      if (!isActive()) return;
      const manual = draft.source.issues.some((issue) => ["document_structural_review","document_classification_review"].includes(issue.issue_code));
      container.innerHTML = `<section class="section-stack expense-workflow">
        <header class="panel-header"><h2>${escape(tr("workflow.reviewAndPostExpense"))}</h2><span>${escape(draft.source.period.period_key)}</span></header>
        <div class="wf-message" role="status">${escape(AutonomoCore.messageText(message, locale))}</div>
        ${error ? `<div class="review-alert error" role="alert" tabindex="-1">${escape(error)}</div>` : ""}
        ${draft.conflict ? `<div class="review-alert warning">${escape(tr("workflow.sourceChangedTheSavedDraftIsRetainedCompareIt"))}<button type="button" id="wf-reset">${escape(tr("workflow.useCurrentFacts"))}</button></div>` : ""}
        ${posted ? `<section class="panel wf-fields"><strong>${escape(tr("workflow.postedInLedger"))}</strong><p>${escape(tr("workflow.thisIsNotAPaymentOrTaxFiling"))}</p>${posted.follow_up_pending ? `<p>${escape(tr("workflow.refreshOrInboxCleanupNeedsRetryDoNotRepost"))}</p><button id="wf-follow-up" type="button">${escape(tr("workflow.retryFollowUp"))}</button>` : ""}<button id="wf-open-posted" type="button">${escape(tr("workflow.openExpense"))}</button></section>` : `
        <div class="wf-layout"><aside class="panel wf-source"><a href="${sourceUrl}" target="_blank" rel="noreferrer">${escape(tr("workflow.openOriginal"))}</a><iframe title="${escape(tr("workflow.sourceDocument"))}" src="${sourceUrl.replace(/content$/, "preview")}"></iframe></aside>
        <form id="wf-form" class="panel wf-fields"><fieldset ${busy ? "disabled" : ""}>
          <h3>${escape(tr("workflow.documentFacts"))}</h3><div class="wf-grid">
          ${field("facts.document_number",tr("workflow.documentNumber"),payload.facts.document_number)}
          ${field("facts.issued_on",tr("workflow.issueDate"),payload.facts.issued_on,"text","date")}
          ${field("facts.transaction_date",tr("workflow.accountingDate"),payload.facts.transaction_date,"text","date")}
          ${field("facts.booking_date",tr("workflow.bookingDate"),payload.facts.booking_date,"text","date")}
          ${field("facts.currency",tr("workflow.originalCurrency"),payload.facts.currency)}
          ${field("facts.gross_minor",tr("workflow.grossInOriginalCurrency"),payload.facts.gross_minor,"money","number")}
          </div>
          <label><span>${escape(tr("workflow.findSupplierByNameTaxId"))}</span><input id="wf-supplier-search" type="search" value="${escape(ui.supplierSearch)}"></label>
          ${select("facts.counterparty_id",tr("workflow.supplier"),payload.facts.counterparty_id,suppliers.map((s) => [s.counterparty_id,`${s.display_name} · ${s.tax_id || s.vat_id || "—"}`]))}
          <button id="wf-new-supplier" class="secondary-button" type="button">${escape(tr("workflow.newSupplier"))}</button>
          ${payload.supplier ? `<div class="wf-grid">${field("supplier.display_name",tr("workflow.name"),payload.supplier.display_name)}${field("supplier.country_code",tr("workflow.countryEsUs"),payload.supplier.country_code)}${field("supplier.tax_id",tr("workflow.taxIdentifier"),payload.supplier.tax_id)}${field("supplier.vat_id",tr("fields.vatId"),payload.supplier.vat_id)}</div>` : ""}
          ${select("facts.business_activity_id",tr("workflow.businessActivity"),payload.facts.business_activity_id,draft.activities.map((a) => [a.business_activity_id,a.description]))}
          ${field("decision.business_purpose",tr("workflow.businessPurpose"),payload.decision.business_purpose)}
          ${field("decision.reason",tr("workflow.decisionReason"),payload.decision.reason)}
          ${field("change_reason",tr("workflow.reasonForFactCorrectionInvalidatingApproval"),payload.change_reason)}
          ${checkbox("decision.document_valid",tr("workflow.originalIsReadableFactsAndAmountsHaveBeenChecked"),payload.decision.document_valid)}
          ${manual ? field("manual_review_reason",tr("workflow.manualReviewExplanationAfterExtractionIssue"),payload.manual_review_reason) : ""}
          <h3>${escape(tr("workflow.treatmentAndDeductionsEur"))}</h3>
          <label><span>${escape(tr("workflow.expenseType"))}</span><select id="wf-kind"><option value="ordinary"${!payload.asset ? " selected" : ""}>${escape(tr("workflow.ordinaryExpense"))}</option><option value="asset"${payload.asset ? " selected" : ""}>${escape(tr("workflow.equipment"))}</option></select></label>
          ${select("decision.tax_treatment.tax_code",tr("workflow.taxClassification"),tax().tax_code,(draft.allowed_values.tax_code || []).filter((x) => !["domestic_output","domestic_output_zero","eu_goods_income","eu_service_income","export","outside_scope"].includes(x)).map((x) => [x,options.taxLabel ? options.taxLabel(x) : x]))}
          <div class="wf-grid">${field("decision.tax_treatment.taxable_base_minor",tr("workflow.taxableBaseEur"),tax().taxable_base_minor,"money","number")}${field("decision.tax_treatment.vat_minor",tr("workflow.invoiceIvaEur"),tax().vat_minor,"money","number")}${field("decision.tax_treatment.deductible_vat_minor",tr("workflow.deductibleIvaEur"),tax().deductible_vat_minor,"money","number")}${!payload.asset ? field("decision.tax_treatment.deductible_irpf_minor",tr("workflow.irpfDeductionEur"),tax().deductible_irpf_minor,"money","number") : ""}${field("decision.tax_treatment.rate_basis_points",tr("workflow.ivaRate"),tax().rate_basis_points,"rate","number")}</div>
          ${select("decision.tax_treatment.vat_investment_good",tr("workflow.ivaPurchaseClassification"),tax().vat_investment_good,[[false,tr("workflow.currentPurchaseForIva")],[true,tr("workflow.ivaInvestmentGood")]],"boolean")}
          <small>${escape(tr("workflow.irpfDepreciationDoesNotDetermineTheIvaInvestmentClassification"))}</small>
          ${payload.asset ? `<h3>${escape(tr("workflow.equipmentAndSchedule"))}</h3><div class="wf-grid">${field("asset.description",tr("workflow.description"),payload.asset.description)}${field("asset.placed_in_service_on",tr("workflow.inServiceDate"),payload.asset.placed_in_service_on,"text","date")}${field("asset.basis_minor",tr("workflow.amortizableBasisBeforeBusinessShareEur"),payload.asset.basis_minor,"money","number")}${field("asset.business_use_ratio",tr("workflow.confirmedBusinessUse"),payload.asset.business_use_ratio,"ratio","number")}${select("asset.aeat_asset_type",tr("workflow.assetCategory"),payload.asset.aeat_asset_type,[["23",tr("workflow.computerElectronic")],["21",tr("workflow.machinery")],["24",tr("workflow.furniture")],["25",tr("workflow.installations")],["28",tr("workflow.tools")],["29",tr("workflow.otherEquipment")]])}${select("asset.method",tr("workflow.depreciation"),payload.asset.method,[["immediate",tr("workflow.immediateNewLowValueEquipment")],["linear",tr("workflow.linearSchedule")]])}${payload.asset.method === "linear" ? field("asset.annual_rate_basis_points",tr("workflow.reviewedAnnualRate"),payload.asset.annual_rate_basis_points,"rate","number") : ""}</div>${checkbox("asset.new_equipment",tr("workflow.equipmentWasAcquiredNew"),payload.asset.new_equipment)}<small>${escape(tr("workflow.futureDepreciationRemainsPlannedUntilExplicitlyPosted"))}</small>` : ""}
          ${payload.facts.currency !== "EUR" ? `<section><h3>${escape(tr("workflow.eurConversion"))}</h3><p>${escape(draft.fx_suggestion?.note || draft.fx_suggestion?.eur_per_unit || tr("workflow.saveTheDraftToRefreshTheRateSuggestion"))}</p><button id="wf-use-fx" type="button" ${!["exact","prior"].includes(draft.fx_suggestion?.status) ? "disabled" : ""}>${escape(tr("workflow.confirmSuggestedEcbRate"))}</button><label><span>${escape(tr("workflow.documentedSettlementRate"))}</span><input id="wf-settlement-rate" type="number" step="0.000001"></label><label><span>${escape(tr("workflow.settlementEvidenceReference"))}</span><input id="wf-settlement-ref"></label><button id="wf-settlement" type="button">${escape(tr("workflow.useSettlementRate"))}</button><small>${escape(payload.fx ? tr("workflow.rateSelectedCheckTaxAmountsInEur") : "")}</small></section>` : ""}
          <details ${ui.technicalOpen ? "open" : ""}><summary>${escape(tr("workflow.additionalTaxDecisionFields"))}</summary><div class="wf-grid">${select("decision.tax_treatment.aeat_invoice_type",tr("workflow.aeatDocumentType"),tax().aeat_invoice_type,(draft.allowed_values.aeat_invoice_type || []).map((v)=>[v,v]))}${field("decision.tax_treatment.aeat_operation_key",tr("workflow.operationKey"),tax().aeat_operation_key)}${field("decision.tax_treatment.aeat_operation_qualification",tr("workflow.operationQualification"),tax().aeat_operation_qualification)}${field("decision.tax_treatment.aeat_expense_concept",tr("workflow.expenseConcept"),tax().aeat_expense_concept)}${field("decision.tax_treatment.withholding_minor",tr("workflow.withholdingEur"),tax().withholding_minor,"money","number")}${field("decision.tax_treatment.deductible_ratio",tr("workflow.deductibleIvaShare"),tax().deductible_ratio,"ratio","number")}</div>${checkbox("decision.tax_treatment.aeat_reverse_charge",tr("workflow.reverseCharge"),tax().aeat_reverse_charge)}${[130,303,347].map((f)=>checkbox(`decision.tax_treatment.include_modelo${f}`,`Modelo ${f}`,tax()[`include_modelo${f}`])).join("")}${field("decision.tax_treatment.notes",tr("workflow.decisionNoteAndSource"),tax().notes)}</details>
          <footer class="wf-actions"><button type="button" id="wf-save" class="secondary-button">${escape(tr("workflow.saveDraft"))}</button><button type="submit" class="primary-button" ${draft.conflict ? "disabled" : ""}>${escape(tr("workflow.previewResult"))}</button></footer>
        </fieldset></form></div>
        ${proposed ? `<section id="wf-preview" class="panel wf-fields" aria-label="${escape(tr("workflow.postingPreview"))}"><h3>${escape(tr("workflow.beforePosting"))}</h3><p>${escape(proposed.supplier)} · ${escape(proposed.period)}</p><dl class="review-facts-list"><div><dt>${escape(tr("workflow.purchase"))}</dt><dd>${money(proposed.gross_minor)} ${escape(proposed.currency)}</dd></div><div><dt>IVA</dt><dd>${money(proposed.deductible_vat_minor)} EUR</dd></div><div><dt>${escape(tr("workflow.irpfDeductionNow"))}</dt><dd>${money(proposed.deductible_irpf_minor)} EUR</dd></div><div><dt>${escape(tr("workflow.laterDepreciation"))}</dt><dd>${money(proposed.future_depreciation_minor)} EUR</dd></div></dl>${scheduleTable(proposed.schedule, tr)}<p>${escape(tr("workflow.onlyThisExpenseAndTheShownDepreciationNoPayment"))}</p><button id="wf-confirm" class="primary-button" type="button" ${busy ? "disabled" : ""}>${escape(tr("workflow.confirmAndPost"))}</button></section>` : ""}`}
      </section>`;
      wire();
    }
    async function task(callback) {
      if (busy) return;
      busy = true; error = ""; render();
      try { await callback(); } catch (err) { error = err.message; }
      finally { busy = false; render(); if (error) container.querySelector('[role="alert"]')?.focus(); }
    }
    async function save() {
      draft = await api(base + "/save", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({payload,expected_version:draft.draft_version,source_snapshot_hash:draft.source_snapshot_hash})});
      payload = copy(draft.payload); message = AutonomoCore.message("workflow.draftSavedOnTheServer");
    }
    function wire() {
      container.querySelectorAll("[data-p]").forEach((input) => input.addEventListener("input", () => {
        if (busy) return;
        const path = input.dataset.p; set(payload,path,read(input)); proposed = null; request = null; container.querySelector("#wf-preview")?.remove();
        if (path === "decision.tax_treatment.tax_code") {
          tax().include_modelo130 = !payload.asset;
          tax().include_modelo303 = true;
          tax().include_modelo347 = true;
          tax().aeat_operation_key = ["eu_service_expense","eu_goods_expense"].includes(tax().tax_code) ? "09" : "01";
          tax().aeat_reverse_charge = ["eu_service_expense","eu_goods_expense","non_eu_service_expense","domestic_reverse_charge_expense"].includes(tax().tax_code);
        }
        if (path === "facts.counterparty_id") { payload.supplier = null; render(); }
        if (path === "asset.method") { payload.asset.annual_rate_basis_points = payload.asset.method === "immediate" ? 10000 : null; render(); }
        if (path === "facts.currency") { payload.facts.currency = input.value.toUpperCase(); payload.fx = null; }
      }));
      container.querySelector("#wf-kind")?.addEventListener("change", (event) => {
        if (busy) return;
        payload.asset = event.target.value === "asset" ? {description:"",basis_minor:tax().taxable_base_minor,business_use_ratio:1,
          annual_rate_basis_points:10000,placed_in_service_on:payload.facts.transaction_date,method:"immediate",new_equipment:false,aeat_asset_type:"23"} : null;
        proposed = null; render();
      });
      container.querySelector("#wf-supplier-search")?.addEventListener("input", (event) => {
        const needle = event.target.value.toLocaleLowerCase();
        container.querySelectorAll('[data-p="facts.counterparty_id"] option').forEach((o) => {o.hidden = Boolean(o.value) && !o.textContent.toLocaleLowerCase().includes(needle) && !o.selected;});
      });
      container.querySelector("#wf-new-supplier")?.addEventListener("click", () => {payload.facts.counterparty_id=null;payload.supplier={display_name:"",tax_id:"",vat_id:"",country_code:""};proposed=null;render();});
      container.querySelector("#wf-save")?.addEventListener("click", () => task(async()=>{await save();proposed=null;}));
      container.querySelector("#wf-form")?.addEventListener("submit", (event) => {event.preventDefault();void task(async()=>{await save();proposed=await api(base+"/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_version:draft.draft_version})});request=pendingRequest("expense-post:"+transactionId,proposed.preview_token,draft.draft_version);});});
      container.querySelector("#wf-confirm")?.addEventListener("click",()=>task(async()=>{posted=await api(base+"/confirm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});message="";}));
      container.querySelector("#wf-reset")?.addEventListener("click",()=>{if(root.confirm(tr("workflow.replaceFieldsWithCurrentSourceValuesTheSavedDraft"))){payload=copy(draft.source_values);draft.source_snapshot_hash=draft.current_snapshot_hash;draft.conflict=false;defaults();proposed=null;render();}});
      container.querySelector("#wf-follow-up")?.addEventListener("click",()=>task(async()=>{posted={...posted,...await api(base+"/follow-up",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"})};}));
      container.querySelector("#wf-open-posted")?.addEventListener("click",()=>onPosted(posted));
      container.querySelector("#wf-use-fx")?.addEventListener("click",()=>{const s=draft.fx_suggestion;payload.fx={rate_date:s.rate_date,rate:s.eur_per_unit,rate_source:"ecb",source_reference:s.source_reference,raw_observation:s.raw_observation,supersedes_rate_id:null};proposed=null;render();});
      container.querySelector("#wf-settlement")?.addEventListener("click",()=>{payload.fx={rate_date:payload.facts.transaction_date,rate:container.querySelector("#wf-settlement-rate").value,rate_source:"actual_settlement",source_reference:container.querySelector("#wf-settlement-ref").value,raw_observation:JSON.stringify({operator_confirmed:true}),supersedes_rate_id:null};proposed=null;render();});
    }
    if (!draft.editable) {onPosted({transaction_id:transactionId});return;}
    render();
    return {
      isBusy: () => busy,
      setLocale(value) {
        if (!isActive()) return;
        const focus = AutonomoCore.captureFocus(container);
        ui.supplierSearch = container.querySelector("#wf-supplier-search")?.value || "";
        ui.settlementRate = container.querySelector("#wf-settlement-rate")?.value || "";
        ui.settlementReference = container.querySelector("#wf-settlement-ref")?.value || "";
        ui.technicalOpen = Boolean(container.querySelector("details")?.open);
        locale = AutonomoCore.normalizeLocale(value);
        render();
        for (const [selector, content] of [["#wf-settlement-rate", ui.settlementRate], ["#wf-settlement-ref", ui.settlementReference]]) {
          const field = container.querySelector(selector);
          if (field) field.value = content;
        }
        if (ui.supplierSearch) {
          const needle = ui.supplierSearch.toLocaleLowerCase();
          container.querySelectorAll('[data-p="facts.counterparty_id"] option').forEach(option => {option.hidden = Boolean(option.value) && !option.textContent.toLocaleLowerCase().includes(needle) && !option.selected;});
        }
        AutonomoCore.restoreFocus(container, focus);
      },
      dispose() { active = false; },
    };
  }
  function scheduleTable(rows, tr) {
    if (!rows.length) return "";
    return `<div class="table-wrap"><table><thead><tr><th>${escape(tr("workflow.period"))}</th><th>${escape(tr("workflow.recognitionDate"))}</th><th>EUR</th></tr></thead><tbody>${rows.map((r)=>`<tr><td>${escape(r.period_key)}</td><td>${escape(r.recognition_on || "—")}</td><td>${money(r.amount_minor)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  async function showSchedule(options) {
    const {container, api, assetId, isActive, locale="ru"} = options;
    const tr = (key, values = {}) => AutonomoCore.formatMessage(key, values, locale);
    const data=await api(`/api/assets/${encodeURIComponent(assetId)}/schedule`);
    if (!isActive()) return;
    container.innerHTML=`<section class="panel wf-fields"><h3>${escape(tr("workflow.scheduleAndRecognizedDepreciation"))}</h3><div role="status" id="wf-schedule-status"></div><div class="table-wrap"><table><thead><tr><th>${escape(tr("workflow.period"))}</th><th>EUR</th><th>${escape(tr("workflow.status"))}</th><th></th></tr></thead><tbody>${data.rows.map((r)=>`<tr><td>${escape(r.period_key)}</td><td>${money(r.amount_minor)}</td><td>${escape(r.recognition_transaction_id ? tr("workflow.posted") : data.native ? tr("workflow.planned") : tr("workflow.historicalRecord"))}</td><td>${r.can_post ? `<button data-recognize="${escape(r.amortization_entry_id)}" type="button">${escape(tr("workflow.post"))} ${escape(r.period_key)}</button>` : escape(r.recognition_on || "")}</td></tr>`).join("")}</tbody></table></div></section>`;
    container.querySelectorAll("[data-recognize]").forEach((button)=>button.addEventListener("click",async()=>{
      container.querySelectorAll("button").forEach((b)=>b.disabled=true);
      const row=data.rows.find((r)=>r.amortization_entry_id===button.dataset.recognize);
      const pending=pendingRequest("depreciation-post:"+row.amortization_entry_id,"depreciation",row.row_version);
      let posted = false;
      try {
        const result=await api(`/api/depreciation/${encodeURIComponent(row.amortization_entry_id)}/post`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({request_id:pending.request_id,expected_version:row.row_version})});
        posted = true;
        if (options.onPosted && isActive()) {await options.onPosted(result);return;}
        await showSchedule(options);
        if(isActive()) container.querySelector('[role="status"]').textContent=result.follow_up_pending ? tr("workflow.postedCalculationRefreshNeedsRetry") : tr("workflow.depreciationPosted");
      } catch(error) {
        if(isActive()){
          container.querySelector('[role="status"]').textContent=posted
            ? tr("workflow.depreciationPostedReloadThePageToRetrieveCurrentData")
            : error.message;
          if (!posted) container.querySelectorAll("button").forEach((b)=>b.disabled=false);
        }
      }
    }));
  }
  const exported={open,showSchedule,read,pendingRequest,scheduleTable};
  if (typeof module !== "undefined" && module.exports) module.exports=exported;
  root.ExpenseWorkflow=exported;
})(typeof window !== "undefined" ? window : globalThis);
