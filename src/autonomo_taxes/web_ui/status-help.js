/* Read-only, domain-aware explanations. No posting or decision writes here. */
(() => {
  const words = {
    ru: {
      details: "Разобраться",
      close: "Закрыть пояснение",
      known: "Что известно",
      reasons: "Что требует внимания",
      next: "Следующие действия",
      sources: "Связанные документы",
      technical: "Исходные сведения",
      review: "Открыть проверку",
      file: "Открыть файл",
      missingFile: "Файл недоступен: проверьте оригинал в хранилище",
      missing: "Нет данных",
      none: "Для выбранного квартала нет записей графика",
      book: "В графике книг",
      excluded: "Не включено в книги",
      annual: "Годовые подтверждения — отдельно, не прибавляются",
      adjustment: "Корректировка",
      noDecision:
        "Отдельное решение не сохранено. Это не доказывает отсутствие прошлых вычетов.",
      copy: "Скопировать вопрос",
      copied: "Вопрос скопирован. Он никуда не отправлен.",
      copyError: "Не удалось скопировать. Выделите текст вопроса ниже.",
      sourceNote:
        "Запись графика или включение в книги не подтверждает подачу декларации.",
      safe: "Просмотр пояснений ничего не проводит и не меняет налоговые решения.",
      original: "Оригинальное сообщение",
      noAction:
        "Для этой записи нет доступной формы подтверждения. Сверьте источники и передайте результат бухгалтеру или оператору учёта.",
      noActionNeeded: "Сейчас действий не требуется.",
      help: "Пояснение",
      calculation: "Расчёт не сформирован",
      refresh: "Обновить расчёт",
      refreshHint: "Пересчитать по текущим данным. Декларация не отправляется.",
      scope: "Амортизация за",
      allAssets: "Все активы; суммы графика за выбранный квартал",
      valueProposal: "Предлагаемый вычет; ещё не подтверждает учёт в налогах",
      afterReview: "Для проведения сначала требуется проверка",
      dated: "Не раньше",
      ready: "Можно провести сейчас",
      blocked: "Проведение заблокировано",
      future: "Можно провести с",
      advisory: "Есть неблокирующие замечания",
    },
    en: {
      details: "Understand status",
      close: "Close explanation",
      known: "What is known",
      reasons: "What needs attention",
      next: "Next actions",
      sources: "Related documents",
      technical: "Source details",
      review: "Open review",
      file: "Open file",
      missingFile: "File unavailable: check the original in storage",
      missing: "No data",
      none: "No schedule entries for the selected quarter",
      book: "In the book schedule",
      excluded: "Not included in books",
      annual: "Annual evidence — separate, not added",
      adjustment: "Adjustment",
      noDecision:
        "No separate decision was saved. This does not prove that earlier deductions were absent.",
      copy: "Copy question",
      copied: "Question copied. Nothing was sent.",
      copyError: "Could not copy. Select the question text below.",
      sourceNote:
        "A schedule or book entry does not establish that a return was filed.",
      safe: "Viewing explanations does not post transactions or change tax decisions.",
      original: "Original message",
      noAction:
        "No confirmation form is available for this record. Check the sources and pass the result to your accountant or ledger operator.",
      noActionNeeded: "No action is needed now.",
      help: "Explanation",
      calculation: "Calculation not generated",
      refresh: "Refresh calculation",
      refreshHint: "Recalculate using current data. No return is submitted.",
      scope: "Amortization for",
      allAssets: "All assets; schedule amounts for the selected quarter",
      valueProposal: "Proposed deduction; not proof of tax recognition",
      afterReview: "Review is required before posting",
      dated: "Not before",
      ready: "Ready to post",
      blocked: "Posting blocked",
      future: "Can be posted from",
      advisory: "Nonblocking notes exist",
    },
  };
  const statuses = {
    received: ["Документ получен", "Document received"],
    extracted: ["Данные извлечены", "Data extracted"],
    needs_review: ["Требуется проверка", "Review required"],
    approved: ["Проверено, ещё не проведено", "Reviewed, not posted"],
    ready: ["Готово к проведению", "Ready to post"],
    deferred: ["Ожидает даты проведения", "Waiting for posting date"],
    blocked: ["Проведение заблокировано", "Posting blocked"],
    posted: ["Проведено в учёте", "Posted in ledger"],
    included_in_snapshot: [
      "Есть запись в снимке декларации",
      "Recorded in a filing snapshot",
    ],
    duplicate: ["Дубликат", "Duplicate"],
    rejected: ["Отклонено", "Rejected"],
    void: ["Аннулировано", "Void"],
    unknown: ["Состояние требует уточнения", "Status needs clarification"],
    recorded_information: ["Есть исходные записи", "Source records exist"],
    warning: ["Есть замечание", "A note needs attention"],
    due: ["Ещё не подано", "Not yet filed"],
    not_due: ["Подача не требуется", "Filing not required"],
    filed: ["Подача подтверждена", "Filing confirmed"],
    waived: [
      "Подача не требуется по сохранённому решению",
      "Filing waived by saved decision",
    ],
  };
  // [title, explanation, next action]. Text is keyed by codes, never by messages.
  const reasons = {
    inbox_roots_not_configured: [
      [
        "Не настроено хранилище оригиналов",
        "Original storage is not configured",
      ],
      [
        "Система не может проверить безопасную обработку исходного файла.",
        "The system cannot verify safe source-file handling.",
      ],
      [
        "Попросите оператора настроить Inbox и архив, затем обновите готовность.",
        "Ask the operator to configure Inbox and archive, then refresh readiness.",
      ],
    ],
    cleanup_precondition_failed: [
      ["Исходный файл требует проверки", "Source file needs checking"],
      [
        "Проверка файла или архивной копии не пройдена.",
        "The source or archive-copy check failed.",
      ],
      [
        "Сверьте оригинал и архивную копию. Не удаляйте файлы для обхода блокировки.",
        "Check the original and archive copy. Do not delete files to bypass the blocker.",
      ],
    ],
    advance_documents_overlap: [
      ["Пересекающиеся авансовые фактуры", "Overlapping advance invoices"],
      [
        "Несколько документов могут подтверждать один аванс. Нельзя учитывать его повторно.",
        "Several documents may cover the same advance. Do not count it twice.",
      ],
      [
        "Уточните у продавца действующий комплект фактур и приложите подтверждение к проверке.",
        "Ask the supplier which invoice set is valid and attach their confirmation to the review.",
      ],
    ],
    iva_prior_deduction_unconfirmed: [
      ["Нужно проверить предыдущий вычет IVA", "Check earlier VAT deductions"],
      [
        "Не подтверждено, что IVA не был заявлен ранее полностью или частично.",
        "It is not confirmed whether any of this VAT was already deducted.",
      ],
      [
        "Сверьте книги и поданные декларации прошлых периодов. После этого откройте проверку операции.",
        "Check the books and filed returns for earlier periods, then open the transaction review.",
      ],
    ],
    future_dated: [
      ["Будущая дата операции", "Future transaction date"],
      [
        "Дата операции ещё не наступила. Наличие других блокировок проверяется отдельно.",
        "The transaction date has not arrived. Other blockers must also be resolved.",
      ],
      [
        "Дождитесь указанной даты и повторно проверьте готовность.",
        "Wait until the date and refresh posting readiness.",
      ],
    ],
    period_not_open: [
      ["Период закрыт для изменений", "Period is closed"],
      [
        "Проведение в этом периоде недоступно.",
        "Posting into this period is unavailable.",
      ],
      [
        "Откройте сведения периода. Не меняйте дату операции для обхода ограничения.",
        "Check the period details. Do not change the transaction date to bypass the restriction.",
      ],
    ],
    lifecycle_not_approved: [
      ["Решение ещё не подтверждено", "Decision not yet confirmed"],
      [
        "Документ и налоговое решение должны пройти проверку.",
        "The document and tax decision need review.",
      ],
      [
        "Откройте проверку операции и заполните необходимые сведения.",
        "Open the transaction review and provide the required information.",
      ],
    ],
    tax_treatment_missing: [
      ["Нет налогового решения", "Tax treatment missing"],
      [
        "Не определено, как учитывать операцию в IRPF и IVA.",
        "The IRPF and VAT treatment has not been set.",
      ],
      [
        "Откройте проверку и укажите обоснованное налоговое решение.",
        "Open review and specify a supported tax treatment.",
      ],
    ],
    tax_treatment_unknown: [
      ["Налоговое решение не определено", "Tax treatment unknown"],
      [
        "Без подтверждённой классификации проведение недоступно.",
        "Posting requires a confirmed classification.",
      ],
      [
        "Выберите налоговое решение в форме проверки.",
        "Select a tax treatment in the review form.",
      ],
    ],
    fx_missing: [
      ["Не хватает суммы в евро", "EUR amount missing"],
      [
        "Для валютной операции не установлен подтверждённый пересчёт.",
        "A supported currency conversion is missing.",
      ],
      [
        "Откройте проверку курса и его источника.",
        "Review the exchange rate and its source.",
      ],
    ],
    fx_unsourced: [
      ["Не подтверждён источник курса", "Exchange rate source unconfirmed"],
      [
        "Курс должен иметь проверяемое основание.",
        "The exchange rate needs a verifiable source.",
      ],
      [
        "Откройте проверку и добавьте источник курса.",
        "Open review and provide the exchange rate source.",
      ],
    ],
    fx_historical_only: [
      ["Курс предназначен для сверки истории", "Historical verification rate"],
      [
        "Этот курс не разрешён для производственного расчёта.",
        "This rate is not allowed for production calculation.",
      ],
      [
        "Укажите подходящий подтверждённый курс в проверке.",
        "Provide an appropriate supported rate in review.",
      ],
    ],
    document_not_ready: [
      ["Документ ещё не проверен", "Document not yet reviewed"],
      [
        "Связанный документ не готов к проведению.",
        "The linked document is not ready for posting.",
      ],
      [
        "Откройте оригинал и завершите проверку документа.",
        "Open the original and complete its review.",
      ],
    ],
    amount_mismatch: [
      ["Суммы расходятся с источником", "Amounts differ from source"],
      [
        "Сумма, валюта или налоговая база требуют сверки.",
        "The amount, currency or taxable base needs reconciliation.",
      ],
      [
        "Сверьте оригинал документа и данные операции.",
        "Compare the original document with the transaction.",
      ],
    ],
    counterparty_tax_profile_review: [
      ["Нужны реквизиты контрагента", "Counterparty details needed"],
      [
        "Страна и налоговые реквизиты требуют подтверждения.",
        "Country and tax identity need confirmation.",
      ],
      [
        "Заполните реквизиты в проверке связанной операции.",
        "Complete the details in the related transaction review.",
      ],
    ],
    foreign_counterparty_identity_missing: [
      ["Не подтверждена налоговая идентификация", "Tax identity unconfirmed"],
      [
        "Для иностранного контрагента не хватает подтверждённых реквизитов.",
        "Verified foreign counterparty details are missing.",
      ],
      [
        "Откройте проверку реквизитов и приложите источник.",
        "Review the identity details and provide supporting evidence.",
      ],
    ],
    deductibility_pending_confirmation: [
      ["Вычет требует подтверждения", "Deduction needs confirmation"],
      [
        "Предлагаемая сумма пока не подтверждает право на вычет.",
        "A proposed amount does not establish deductibility.",
      ],
      [
        "Сверьте документы и подтвердите допустимую сумму в проверке.",
        "Check the evidence and confirm the allowable amount in review.",
      ],
    ],
    document_classification_review: [
      ["Неясен тип документа", "Document type unclear"],
      [
        "Автоматически определить тип документа не удалось.",
        "The document could not be classified automatically.",
      ],
      [
        "Откройте оригинал и проверьте его назначение.",
        "Open the original and check its purpose.",
      ],
    ],
    document_structural_review: [
      [
        "Нужно проверить читаемость документа",
        "Document readability needs review",
      ],
      [
        "Извлечение данных обнаружило проблему структуры документа.",
        "Extraction identified a document structure problem.",
      ],
      [
        "Откройте оригинал; при необходимости получите исправленный файл.",
        "Open the original and obtain a corrected file if needed.",
      ],
    ],
    missing_locally: [
      ["Оригинал недоступен", "Original unavailable"],
      [
        "Сведения о документе есть, но оригинал не найден.",
        "A document record exists, but its original was not found.",
      ],
      [
        "Проверьте хранилище и восстановите связь с оригиналом.",
        "Check storage and restore the link to the original.",
      ],
    ],
    transaction_tax_review: [
      ["Нужно проверить учёт операции", "Transaction treatment needs review"],
      [
        "Назначение операции и налоговый вычет требуют решения.",
        "The business purpose and deduction need a decision.",
      ],
      [
        "Откройте проверку операции и сверьте документы.",
        "Open transaction review and check its documents.",
      ],
    ],
    modelo100_deferral_submission_unconfirmed: [
      ["Отсрочка не подтверждена", "Deferral unconfirmed"],
      [
        "Нет подтверждения подачи заявления на отсрочку.",
        "Submission of the deferral application is unconfirmed.",
      ],
      [
        "Найдите подтверждение подачи и сверьте его с обязательством.",
        "Find the submission receipt and reconcile it with the obligation.",
      ],
    ],
    modelo130_difficult_expense_filed_method_conflict: [
      [
        "Метод расчёта требует сверки",
        "Calculation method needs reconciliation",
      ],
      [
        "Сохранённый метод отличается от ожидаемого правила.",
        "The saved method differs from the expected rule.",
      ],
      [
        "Сверьте источник и поданную декларацию; не изменяйте её автоматически.",
        "Compare the source and filed return; do not automatically change it.",
      ],
    ],
    modelo303_q1_notary_vat_base_unverified: [
      ["База IVA требует сверки", "VAT base needs reconciliation"],
      [
        "Налоговая база по исходному счёту не подтверждена.",
        "The source invoice taxable base is unconfirmed.",
      ],
      [
        "Сверьте счёт и сумму, заявленную в декларации.",
        "Compare the invoice with the amount in the filed return.",
      ],
    ],
    modelo303_q1_output_vat_classification_mismatch: [
      ["Классификация IVA расходится", "VAT classification differs"],
      [
        "Классификация расходится с поданной декларацией.",
        "The classification differs from the filed return.",
      ],
      [
        "Сверьте исходные операции с декларацией.",
        "Reconcile the source transactions with the return.",
      ],
    ],
    nonresident_payee_legal_form_review: [
      ["Нужна правовая форма получателя", "Payee legal form needed"],
      [
        "От неё зависит проверка иностранной выплаты.",
        "It affects the review of the foreign payment.",
      ],
      [
        "Уточните правовую форму в проверке контрагента.",
        "Confirm the legal form in counterparty review.",
      ],
    ],
    nonresident_professional_irnr_review: [
      ["Требуется проверка IRNR", "IRNR review required"],
      [
        "Выплата иностранному исполнителю требует отдельной проверки.",
        "The foreign supplier payment needs a separate review.",
      ],
      [
        "Откройте сведения выплаты и проверьте обязательства IRNR.",
        "Open the payment details and review IRNR obligations.",
      ],
    ],
  };
  const terms = {
    IVA: [
      "IVA — НДС. Сумма в предложении к проверке ещё не означает, что вычет заявлен.",
      "IVA is VAT. A proposed amount does not mean that a deduction has been claimed.",
    ],
    IRPF: [
      "IRPF — налог на доход физических лиц. Покупка актива и его амортизация учитываются отдельно.",
      "IRPF is personal income tax. Asset acquisition and depreciation are treated separately.",
    ],
    ROI: [
      "ROI — реестр участников внутрисоюзных операций. «Не проверено» не означает «не зарегистрирован».",
      "ROI is the intra-community operators register. Unverified does not mean unregistered.",
    ],
    base: [
      "Амортизируемая база — сумма, от которой рассчитан график. При неподтверждённом IVA база может быть предварительной.",
      "The depreciable base is the amount used for the schedule. Pending VAT treatment can make it provisional.",
    ],
    rate: [
      "Годовая ставка амортизации. Она не равна сумме вычета за квартал.",
      "Annual depreciation rate. It is not the quarterly deduction amount.",
    ],
    posting: [
      "Проведение фиксирует операцию в учёте. Оно не отправляет декларацию в AEAT.",
      "Posting records a transaction in the ledger. It does not submit a return to AEAT.",
    ],
    forecast: [
      "График и прогноз не доказывают, что расход учтён или декларация подана.",
      "A schedule or forecast does not establish expense recognition or filing.",
    ],
    paymentDeadline: [
      "Срок для выбора списания налога со счёта. Он может наступить раньше срока подачи.",
      "Deadline to choose tax payment by direct debit. It can precede the filing deadline.",
    ],
    cost: [
      "Полная стоимость покупки; она может отличаться от остатка к оплате в фактуре после зачёта авансов.",
      "Full purchase cost; it may differ from the invoice balance after advance settlement.",
    ],
    use: [
      "Подтверждённая доля использования для деятельности. Не выводится автоматически из типа устройства.",
      "Confirmed business-use share. It is not inferred from the device type.",
    ],
  };
  const fields = {
    transaction_date: ["Дата операции", "Transaction date"],
    issued_on: ["Дата документа", "Document date"],
    amount_minor: [
      "Сумма документа / операции",
      "Document / transaction amount",
    ],
    cost_minor: ["Стоимость покупки", "Purchase cost"],
    amortizable_base_minor: ["База амортизации", "Depreciable base"],
    placed_in_service_on: ["Начало использования", "In-service date"],
    business_use_ratio: ["Рабочее использование", "Business use"],
    annual_rate_basis_points: ["Годовая ставка", "Annual rate"],
    advisor_decision: ["Сохранённое решение", "Saved decision"],
    determination: ["Обязанность подачи", "Filing obligation"],
    filing_status: ["Состояние подачи", "Filing status"],
    filed_at: ["Дата подачи", "Filed on"],
    statutory_due_on: ["Срок подачи", "Filing deadline"],
    direct_debit_cutoff_on: ["Списание со счёта до", "Direct debit cutoff"],
    deadline_status: ["Подтверждение срока", "Deadline certainty"],
    country_code: ["Страна", "Country"],
    tax_id: ["Налоговый номер", "Tax ID"],
    vat_id: ["VAT ID", "VAT ID"],
    roi_status: ["Регистрация в ROI", "ROI registration"],
    lifecycle_status: ["Состояние записи", "Record state"],
  };
  const termNames = {
    base: ["Амортизируемая база", "Depreciable base"],
    cost: ["Стоимость", "Cost"],
    rate: ["Годовая ставка", "Annual rate"],
    use: ["Рабочее использование", "Business use"],
    posting: ["Проведение", "Posting"],
    forecast: ["График и прогноз", "Schedule and forecast"],
    paymentDeadline: ["Списание налога со счёта", "Tax direct debit"],
  };
  const registry = new Map();
  let sequence = 0;
  let currentLocale = "ru";
  let opener = null;
  let scrollY = 0;
  let panelUrl = "";
  let pendingNavigation = null;
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
  const lang = (pair) => pair[currentLocale === "en" ? 1 : 0];
  const word = (key) => words[currentLocale][key] || key;
  const money = (v, currency = "EUR") =>
    v == null ||
    v === "" ||
    !Number.isFinite(Number(v)) ||
    !/^[A-Z]{3}$/.test(currency)
      ? word("missing")
      : new Intl.NumberFormat(currentLocale === "ru" ? "ru-RU" : "en-GB", {
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
          currentLocale === "ru" ? "ru-RU" : "en-GB",
        ).format(date);
  }
  function reasonKey(r) {
    return r.details?.issue_code || r.code || "unknown";
  }
  function reasonInfo(r) {
    return (
      reasons[r.code] ||
      reasons[reasonKey(r)] || [
        ["Причина требует уточнения", "Reason needs clarification"],
        [
          "Для этой причины пока нет подробной инструкции. Исходное сообщение доступно ниже.",
          "No detailed guidance exists for this reason yet. The source message is available below.",
        ],
        [
          "Откройте связанную проверку и передайте исходные сведения бухгалтеру при необходимости.",
          "Open the related review and share the source details with your accountant if needed.",
        ],
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
          unknown: [
            "Регистрация в ROI не проверена",
            "ROI registration unverified",
          ],
          registered: [
            "Регистрация в ROI подтверждена",
            "ROI registration confirmed",
          ],
          not_registered: ["Не зарегистрирован в ROI", "Not registered in ROI"],
        }[ctx.state] || statuses.unknown,
      );
    if (ctx.domain === "obligation" && ctx.state === "unknown")
      return lang(
        ctx.facts?.determination === "unknown"
          ? ["Нужно проверить обязанность подачи", "Check filing obligation"]
          : ["Подача не подтверждена", "Filing not confirmed"],
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
      return lang([
        "Ожидает проверки авансов и IVA",
        "Awaiting advance and VAT review",
      ]);
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
  function register(ctx) {
    const id = String(++sequence);
    registry.set(id, ctx);
    return id;
  }
  function cell(ctx) {
    ctx = ctx || { domain: "unknown", state: "unknown" };
    const id = register(ctx);
    return `<div class="status-context"><span class="badge status-${tone(ctx)}">${esc(label(ctx))}</span><small>${esc(summary(ctx))}</small><button type="button" class="text-button status-details-button" data-status-help="${id}" aria-haspopup="dialog">${esc(word("details"))}</button></div>`;
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
      return new Intl.NumberFormat(currentLocale === "ru" ? "ru-RU" : "en-GB", {
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
              return `<article class="status-reason"><h4>${i + 1}. ${esc(lang(info[0]))}</h4><p>${esc(lang(info[1]))}</p><p><strong>${esc(word("next"))}:</strong> ${esc(lang(info[2]))}</p>${r.code === "advance_documents_overlap" ? `<p class="copy-question">${esc(lang(["Какие авансовые фактуры по этой покупке действуют и какие заменены или аннулированы? Просьба прислать подтверждение.", "Which advance invoices for this purchase remain valid, and which were replaced or cancelled? Please provide confirmation."]))}</p><button type="button" class="secondary-button" data-copy-question>${esc(word("copy"))}</button><p class="copy-feedback" role="status"></p>` : ""}<details><summary>${esc(word("technical"))}</summary><code>${esc(r.source_code || reasonKey(r))}</code><p>${esc(r.message || word("missing"))}</p></details></article>`;
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
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    window.scrollTo(0, scrollY);
    if (pendingNavigation) {
      const url = pendingNavigation;
      pendingNavigation = null;
      window.location.assign(url);
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
    if (push)
      window.history.pushState(
        { ...window.history.state, accountingHelp: id },
        "",
        panelUrl,
      );
  }
  function close() {
    if (window.history.state?.accountingHelp && dialog()?.open)
      window.history.back();
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
    labelTables,
    setLocale: (l) => {
      currentLocale = l === "en" ? "en" : "ru";
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
    allReasons,
    content,
    beforeRender,
    handlePopState,
  };
})();
