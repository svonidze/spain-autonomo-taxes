const LOCALE_STORAGE_KEY = "autonomo.locale";
const REVIEW_DRAFT_STORAGE_PREFIX = "autonomo.review-draft";
const SUPPORTED_LOCALES = new Set(["ru", "en"]);
const KNOWN_REVIEW_TAX_CODES = new Set([
  "domestic_input",
  "domestic_output",
  "domestic_output_zero",
  "eu_goods_income",
  "eu_service_income",
  "export",
  "outside_scope",
  "reverse_charge",
  "withholding_service",
  "withholding_rent",
  "unknown",
]);

const messages = {
  ru: {
    "app.title": "Учет autónomo",
    "brand.subtitle": "Бухгалтерия в Испании",
    "nav.aria": "Основная навигация",
    "nav.dashboard": "Обзор",
    "nav.income": "Доходы",
    "nav.expenses": "Расходы",
    "nav.review": "Проверка",
    "nav.assets": "Активы",
    "nav.taxes": "Налоги",
    "nav.contacts": "Контрагенты",
    "storage.local": "Локальная SQLite",
    "locale.aria": "Язык интерфейса",
    "toolbar.period": "Период",
    "toolbar.periodAria": "Налоговый период",
    "toolbar.refresh": "Обновить расчет",
    "common.add": "Добавить",
    "common.copyToPeriod": "Копировать в {period}",
    "common.back": "Назад",
    "common.cancel": "Отмена",
    "common.close": "Закрыть",
    "common.file": "Файл",
    "common.loading": "Загрузка данных…",
    "common.noId": "без ID",
    "common.noRecords": "Нет записей",
    "common.refresh": "Обновить",
    "common.save": "Сохранить",
    "common.step": "Шаг",
    "common.yes": "Да",
    "common.no": "Нет",
    "intake.title": "Новая запись",
    "intake.kindAria": "Тип записи",
    "intake.expenseFile": "Выберите счет поставщика или перетащите файл",
    "intake.incomeFile": "Выберите выставленный счет или перетащите файл",
    "intake.fileFormats": "PDF, изображение, CSV или TXT до 30 MB",
    "intake.accept": "Принять в систему",
    "intake.selectFile": "Выберите файл.",
    "intake.processing": "Извлечение и запись…",
    "intake.failed": "Не удалось принять документ",
    "intake.accepted": "Принято: {id}",
    "intake.acceptedToast": "Документ принят в {period}",
    "intake.copyNotice": "Дата и номер перенесены в {period}, когда это безопасно. Проверьте поля и загрузите новый файл перед приемом.",
    "intake.copyUnsupportedCurrency": "Валюта {currency} не поддерживается формой приема, поэтому валюта и сумма не были скопированы.",
    "intake.copyInvalidAmount": "Сумма источника недоступна для копирования, поэтому введите ее вручную при необходимости.",
    "intake.copyStale": "Исходная строка больше недоступна. Обновите страницу или повторите поиск.",
    "errors.apiReturnedHtml": "API вернул HTML вместо данных. Вероятно, на {origin} запущен другой сервер. Остановите его или запустите autonomo-web на другом порту.",
    "errors.unexpectedNonJson": "Неожиданный ответ не в JSON от {url} ({status}).",
    "errors.malformedJson": "Некорректный JSON в ответе от {url} ({status}).",
    "fields.amount": "Сумма",
    "fields.bookingDate": "Дата проводки",
    "fields.businessPurpose": "Деловое назначение",
    "fields.supplier": "Поставщик",
    "fields.client": "Клиент",
    "fields.counterpartyCountry": "Страна контрагента",
    "fields.counterpartyType": "Форма контрагента",
    "fields.currency": "Валюта",
    "fields.deductibleIrpfMinor": "Вычет IRPF, центы",
    "fields.deductibleRatio": "Доля вычета",
    "fields.deductibleVatMinor": "Вычет IVA, центы",
    "fields.documentDate": "Дата документа",
    "fields.documentValid": "Документ подтвержден",
    "fields.fxRate": "Курс EUR",
    "fields.fxRateDate": "Дата курса",
    "fields.fxRateSource": "Источник курса",
    "fields.fxSourceReference": "Ссылка или примечание к источнику",
    "fields.includeModelo130": "Включать в Modelo 130",
    "fields.includeModelo303": "Включать в Modelo 303",
    "fields.includeModelo347": "Включать в Modelo 347",
    "fields.invoiceType": "Тип счета AEAT",
    "fields.issuedOn": "Дата счета",
    "fields.legalForm": "Юр. форма",
    "fields.notes": "Примечание для аудита",
    "fields.number": "Номер",
    "fields.operationKey": "Ключ операции AEAT",
    "fields.operationQualification": "Квалификация AEAT",
    "fields.exemptionCode": "Код освобождения AEAT",
    "fields.reverseCharge": "Обратное начисление IVA",
    "fields.expenseConcept": "Код расхода AEAT",
    "fields.rateBasisPoints": "Ставка, базисные пункты",
    "fields.ruleVersion": "Версия правила",
    "fields.outcome": "Решение",
    "fields.reason": "Причина решения",
    "fields.retentionExpected": "Ожидать удержание",
    "fields.professionalSupplier": "Профессиональный поставщик",
    "fields.reviewAction": "Действие по вопросу",
    "fields.reviewReason": "Почему вопрос можно закрыть",
    "fields.roiStatus": "ROI / VAT",
    "fields.supplier": "Поставщик",
    "fields.taxCode": "Налоговый код",
    "fields.taxId": "NIF / Tax ID",
    "fields.taxableBaseMinor": "База, центы",
    "fields.total": "Итого",
    "fields.transactionDate": "Дата операции",
    "fields.vatBase": "База IVA",
    "fields.vatId": "VAT ID",
    "fields.vatMinor": "IVA, центы",
    "fields.withholdingMinor": "Удержание, центы",
    "titles.dashboard": "Обзор",
    "titles.income": "Доходы",
    "titles.expenses": "Расходы",
    "titles.review": "Проверка",
    "titles.assets": "Активы",
    "titles.taxes": "Налоги и сроки",
    "titles.contacts": "Контрагенты",
    "dashboard.incomePosted": "Доходы, проведено",
    "dashboard.expensesPosted": "Расходы, проведено",
    "dashboard.expensesForecast": "Готово к проведению",
    "dashboard.waitingForPeriodOne": "{count} ожидает закрытия периода",
    "dashboard.waitingForPeriodOther": "{count} ожидают закрытия периода",
    "dashboard.calculatedAsOf": "расчет на {date}",
    "dashboard.notCalculated": "расчет не обновлен",
    "dashboard.filed": "подано",
    "dashboard.filedOn": "подано {date}",
    "dashboard.valuesUnavailable": "значения недоступны",
    "dashboard.snapshotAvailable": "есть filing snapshot",
    "dashboard.carryForward": "к переносу {amount}",
    "dashboard.approvedNotPosted": "В периоде есть подтвержденные операции, но они еще не проведены. Карточки «проведено» считают только posted / included in filing.",
    "dashboard.postingBannerTitle": "{count} подтвержденных операций ждут проведения",
    "dashboard.postingBannerCta": "Открыть проверку",
    "dashboard.modelo130Box": "Modelo 130 · поле 19",
    "dashboard.modelo303Result": "Modelo 303 · результат",
    "dashboard.upcomingFiling": "Предстоящая декларация",
    "dashboard.obligationDue": "Требуется подать за выбранный период.",
    "dashboard.recentTransactions": "Последние операции",
    "dashboard.needsAttention": "Требует внимания",
    "dashboard.readyBannerOne": "{count} операция уже проверена и может быть проведена",
    "dashboard.readyBannerOther": "{count} операции уже проверены и могут быть проведены",
    "dashboard.readyBannerAction": "Открыть проверку",
    "transactions.search": "Поиск по контрагенту или номеру",
    "transactions.date": "Дата",
    "transactions.counterpartyDocument": "Контрагент / документ",
    "transactions.status": "Статус",
    "transactions.amount": "Сумма",
    "transactions.irpfDeduction": "Вычет IRPF",
    "transactions.noCounterparty": "Без контрагента",
    "transactions.actionNeeded": "требует решения",
    "review.transactions": "Операции на проверке",
    "review.documents": "Документы на проверке",
    "review.openIssues": "Открытые вопросы",
    "review.summary": "Очередь проведения",
    "review.summaryReady": "Можно провести сейчас",
    "review.summaryNeedsReview": "Нужно проверить",
    "review.summaryLater": "Можно будет провести позже",
    "review.summaryBlocked": "Нельзя провести",
    "review.workspaceBack": "К списку операций",
    "review.workspaceTitle": "Проверка счета",
    "review.openWorkspace": "Открыть проверку",
    "review.facts": "Факты",
    "review.taxDecision": "Налоговое решение",
    "review.result": "Результат",
    "review.requirements": "Что нужно подтвердить",
    "review.requirementSupported": "можно сделать сейчас",
    "review.requirementUnsupported": "нужно завершить вне этого экрана",
    "review.counterpartyFacts": "Проверенные факты о контрагенте",
    "review.counterpartyFactsHint": "Меняются только поля решения. Исходный пакет не редактируется.",
    "review.technicalDetails": "Технические поля AEAT",
    "review.validationPending": "Сначала выполните проверку пакета.",
    "review.validationPassed": "Пакет прошел проверку и готов к применению.",
    "review.validationPreview": "Предпросмотр проверки",
    "review.primaryValidate": "Проверить пакет",
    "review.primaryApprove": "Подтвердить и оставить готовым к проведению",
    "review.primaryReject": "Отклонить счет",
    "review.applySuccess": "Решение применено.",
    "review.fx": "Курс валюты",
    "review.fxHint": "Курс записывается отдельным вызовом и обновляет рабочий пакет.",
    "review.fxApply": "Применить курс",
    "review.fxApplied": "Курс обновлен. Пакет перезагружен.",
    "review.fxDirty": "После смены курса пакет нужно проверить заново.",
    "review.unsupported": "Этот счет нельзя завершить из этого экрана.",
    "review.future": "Будущую операцию можно провести только после {date}.",
    "review.postingReady": "Проверено · Можно провести сейчас",
    "review.postingLater": "Проверено · Можно будет провести {date}",
    "review.postingBlocked": "Проверено · Нельзя провести",
    "review.needsReview": "Нужно проверить решение по счету",
    "review.unknownSupport": "Налоговый код не поддерживается для проведения.",
    "review.outcomeApprove": "Подтвердить",
    "review.outcomeReject": "Отклонить",
    "review.actionResolve": "Закрыть вопрос",
    "review.actionKeepOpen": "Оставить открытым",
    "review.documentLink": "Открыть файл",
    "review.invoiceLabel": "Счет {number}",
    "review.issueOpen": "Открытый вопрос",
    "review.issueResolved": "Вопрос будет закрыт",
    "review.assetDecision": "Классификация расхода",
    "review.assetCurrentExpense": "Текущий расход",
    "review.assetAsset": "Основное средство",
    "review.assetNotApplicable": "Не применяется",
    "review.supportReason": "Причина блокировки",
    "review.statusReady": "готово",
    "review.statusLater": "позже",
    "review.statusBlocked": "заблокировано",
    "review.stepBusinessPurpose": "Подтвердите деловое назначение расхода.",
    "review.stepIrpfAmount": "Укажите сумму, допустимую к вычету по IRPF.",
    "review.stepIvaTreatment": "Проверьте режим IVA и базу налога.",
    "review.stepIncomeRecognition": "Подтвердите признание дохода.",
    "review.stepAssetDecision": "Решите, это текущий расход или актив.",
    "review.stepBusinessUse": "Укажите процент делового использования.",
    "review.stepFx": "Добавьте официальный или расчетный курс EUR.",
    "review.stepAmountError": "Разберите расхождение сумм: {detail}.",
    "review.stepUnknown": "Подтвердите это требование вручную: {code}.",
    "review.postingTitle": "Проведение подтвержденных операций",
    "review.postingApproved": "Подтверждено",
    "review.postingBatchReady": "Готово к проведению",
    "review.postingDeferred": "Отложено",
    "review.postingBatchBlocked": "Заблокировано",
    "review.postingCleanup": "Очистка Inbox",
    "review.postingCleanupBlocked": "Очистка заблокирована",
    "review.postingAmount": "Сумма к проведению",
    "review.postingOpenPeriod": "Период открыт",
    "review.postingClosedPeriod": "Период закрыт",
    "review.postingGeneratedAt": "Сформировано {date}",
    "review.postingGeneratedUnknown": "Превью проведения без отметки времени",
    "review.postingReadyHint": "Будут проведены только строки, которые все еще готовы по текущему превью.",
    "review.postingNothingReady": "Сейчас нет строк, готовых к проведению.",
    "review.postingClosedHint": "Проведение отключено: выбранный период не открыт.",
    "review.postConfirmTitle": "Провести готовые операции",
    "review.postConfirmLead": "Будут проведены только эти строки из текущего превью.",
    "review.postConfirmAction": "Провести",
    "review.postConfirmCleanupWarning": "Проведение затронет cleanup: применится {cleanupCount}, заблокировано {cleanupBlockedCount}. Проверьте причины перед подтверждением.",
    "review.postingRows": "Строки превью",
    "review.postingRetryRefresh": "Повторить обновление расчета",
    "review.postingStaleWarning": "Операции проведены, налоговый расчёт требует обновления.",
    "review.postingResults": "Результат проведения",
    "review.postingResultSummary": "Статус: {status} · проведено {count}",
    "review.postingResultMessage": "Сообщение",
    "review.postSuccess": "Проведено: {count}",
    "review.postPartial": "Проведение завершено частично",
    "review.postInterrupted": "Проведение прервано",
    "review.postNone": "Новых проводок нет",
    "assets.title": "Активы и амортизация",
    "assets.asset": "Актив",
    "assets.inService": "Ввод",
    "assets.cost": "Стоимость",
    "assets.base": "База",
    "assets.businessUse": "Использование",
    "assets.rate": "Ставка",
    "assets.schedule": "График",
    "assets.decision": "Решение",
    "taxes.obligations": "Обязательства",
    "taxes.form": "Форма",
    "taxes.applicability": "Применимость",
    "taxes.status": "Статус",
    "taxes.directDebit": "Домицилиация",
    "taxes.deadline": "Срок",
    "taxes.noCalculation": "нет расчета",
    "taxes.calculationMissing": "Расчет не сформирован",
    "taxes.filedValuesUnavailable": "Декларация подана, но значения filing snapshot недоступны.",
    "taxes.filedValuesUnavailableExtract": "Декларация подана, но значения не удалось извлечь из filing snapshot.",
    "taxes.filedValuesUnavailablePdf": "Декларация подана, но PDF filing snapshot не удалось прочитать.",
    "contacts.title": "Контрагенты",
    "contacts.name": "Название",
    "contacts.country": "Страна",
    "contacts.transactions": "Операций",
    "contacts.last": "Последняя",
    "refresh.done": "Расчет {period} обновлен",
    "documents.counterparty": "Контрагент",
    "documents.type": "Тип",
    "issues.none": "Открытых вопросов нет",
    "issues.sourceDetails": "Исходные сведения",
  },
  en: {
    "app.title": "Autónomo accounting",
    "brand.subtitle": "Accounting in Spain",
    "nav.aria": "Primary navigation",
    "nav.dashboard": "Overview",
    "nav.income": "Income",
    "nav.expenses": "Expenses",
    "nav.review": "Review",
    "nav.assets": "Assets",
    "nav.taxes": "Taxes",
    "nav.contacts": "Counterparties",
    "storage.local": "Local SQLite",
    "locale.aria": "Interface language",
    "toolbar.period": "Period",
    "toolbar.periodAria": "Tax period",
    "toolbar.refresh": "Refresh calculation",
    "common.add": "Add",
    "common.copyToPeriod": "Copy into {period}",
    "common.back": "Back",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.file": "File",
    "common.loading": "Loading data…",
    "common.noId": "no ID",
    "common.noRecords": "No records",
    "common.refresh": "Refresh",
    "common.save": "Save",
    "common.step": "Step",
    "common.yes": "Yes",
    "common.no": "No",
    "intake.title": "New entry",
    "intake.kindAria": "Entry type",
    "intake.expenseFile": "Choose a supplier invoice or drop a file",
    "intake.incomeFile": "Choose an issued invoice or drop a file",
    "intake.fileFormats": "PDF, image, CSV, or TXT up to 30 MB",
    "intake.accept": "Add to system",
    "intake.selectFile": "Choose a file.",
    "intake.processing": "Extracting and recording…",
    "intake.failed": "Could not accept the document",
    "intake.accepted": "Accepted: {id}",
    "intake.acceptedToast": "Document accepted into {period}",
    "intake.copyNotice": "The date and number were carried into {period} when safe. Review the fields and upload a fresh file before accepting.",
    "intake.copyUnsupportedCurrency": "Currency {currency} is not supported by the intake form, so currency and total were not copied.",
    "intake.copyInvalidAmount": "The source total could not be copied, so enter it manually if needed.",
    "intake.copyStale": "The source row is no longer available. Refresh the page or run the search again.",
    "errors.apiReturnedHtml": "The API returned HTML instead of data. Another server is probably running at {origin}. Stop it or run autonomo-web on another port.",
    "errors.unexpectedNonJson": "Unexpected non-JSON response from {url} ({status}).",
    "errors.malformedJson": "Malformed JSON response from {url} ({status}).",
    "fields.amount": "Amount",
    "fields.bookingDate": "Booking date",
    "fields.businessPurpose": "Business purpose",
    "fields.supplier": "Supplier",
    "fields.client": "Client",
    "fields.counterpartyCountry": "Counterparty country",
    "fields.counterpartyType": "Counterparty form",
    "fields.currency": "Currency",
    "fields.deductibleIrpfMinor": "IRPF deduction, cents",
    "fields.deductibleRatio": "Deduction ratio",
    "fields.deductibleVatMinor": "VAT deduction, cents",
    "fields.documentDate": "Document date",
    "fields.documentValid": "Document confirmed",
    "fields.fxRate": "EUR rate",
    "fields.fxRateDate": "Rate date",
    "fields.fxRateSource": "Rate source",
    "fields.fxSourceReference": "Source link or note",
    "fields.includeModelo130": "Include in Modelo 130",
    "fields.includeModelo303": "Include in Modelo 303",
    "fields.includeModelo347": "Include in Modelo 347",
    "fields.invoiceType": "AEAT invoice type",
    "fields.issuedOn": "Invoice date",
    "fields.legalForm": "Legal form",
    "fields.notes": "Audit note",
    "fields.number": "Number",
    "fields.operationKey": "AEAT operation key",
    "fields.operationQualification": "AEAT qualification",
    "fields.exemptionCode": "AEAT exemption code",
    "fields.reverseCharge": "VAT reverse charge",
    "fields.expenseConcept": "AEAT expense concept",
    "fields.rateBasisPoints": "Rate, basis points",
    "fields.ruleVersion": "Rule version",
    "fields.outcome": "Outcome",
    "fields.reason": "Decision reason",
    "fields.retentionExpected": "Retention expected",
    "fields.professionalSupplier": "Professional supplier",
    "fields.reviewAction": "Issue action",
    "fields.reviewReason": "Why the issue can be resolved",
    "fields.roiStatus": "ROI / VAT",
    "fields.supplier": "Supplier",
    "fields.taxCode": "Tax code",
    "fields.taxId": "NIF / Tax ID",
    "fields.taxableBaseMinor": "Taxable base, cents",
    "fields.total": "Total",
    "fields.transactionDate": "Transaction date",
    "fields.vatBase": "VAT base",
    "fields.vatId": "VAT ID",
    "fields.vatMinor": "VAT, cents",
    "fields.withholdingMinor": "Withholding, cents",
    "titles.dashboard": "Overview",
    "titles.income": "Income",
    "titles.expenses": "Expenses",
    "titles.review": "Review",
    "titles.assets": "Assets",
    "titles.taxes": "Taxes and deadlines",
    "titles.contacts": "Counterparties",
    "dashboard.incomePosted": "Posted income",
    "dashboard.expensesPosted": "Posted expenses",
    "dashboard.expensesForecast": "Ready to post",
    "dashboard.waitingForPeriodOne": "{count} awaiting period close",
    "dashboard.waitingForPeriodOther": "{count} awaiting period close",
    "dashboard.calculatedAsOf": "calculated as of {date}",
    "dashboard.notCalculated": "calculation not refreshed",
    "dashboard.filed": "filed",
    "dashboard.filedOn": "filed {date}",
    "dashboard.valuesUnavailable": "values unavailable",
    "dashboard.snapshotAvailable": "filing snapshot available",
    "dashboard.carryForward": "carry-forward {amount}",
    "dashboard.approvedNotPosted": "This period has approved transactions that are not posted yet. The “posted” cards count only posted / included in filing rows.",
    "dashboard.postingBannerTitle": "{count} approved transactions are waiting to post",
    "dashboard.postingBannerCta": "Open review",
    "dashboard.modelo130Box": "Modelo 130 · box 19",
    "dashboard.modelo303Result": "Modelo 303 · result",
    "dashboard.upcomingFiling": "Upcoming filing",
    "dashboard.obligationDue": "Filing is due for the selected period.",
    "dashboard.recentTransactions": "Recent transactions",
    "dashboard.needsAttention": "Needs attention",
    "dashboard.readyBannerOne": "{count} reviewed transaction can be posted now",
    "dashboard.readyBannerOther": "{count} reviewed transactions can be posted now",
    "dashboard.readyBannerAction": "Open review",
    "transactions.search": "Search by counterparty or number",
    "transactions.date": "Date",
    "transactions.counterpartyDocument": "Counterparty / document",
    "transactions.status": "Status",
    "transactions.amount": "Amount",
    "transactions.irpfDeduction": "IRPF deduction",
    "transactions.noCounterparty": "No counterparty",
    "transactions.actionNeeded": "action needed",
    "review.transactions": "Transactions to review",
    "review.documents": "Documents to review",
    "review.openIssues": "Open issues",
    "review.summary": "Posting queue",
    "review.summaryReady": "Can post now",
    "review.summaryNeedsReview": "Needs review",
    "review.summaryLater": "Can post later",
    "review.summaryBlocked": "Cannot post",
    "review.workspaceBack": "Back to transactions",
    "review.workspaceTitle": "Invoice review",
    "review.openWorkspace": "Open review",
    "review.facts": "Facts",
    "review.taxDecision": "Tax decision",
    "review.result": "Result",
    "review.requirements": "What to confirm",
    "review.requirementSupported": "can be completed here",
    "review.requirementUnsupported": "must be completed outside this screen",
    "review.counterpartyFacts": "Verified counterparty facts",
    "review.counterpartyFactsHint": "Only decision fields change. The immutable packet stays untouched.",
    "review.technicalDetails": "Technical AEAT fields",
    "review.validationPending": "Validate the packet before applying it.",
    "review.validationPassed": "The packet validated successfully and is ready to apply.",
    "review.validationPreview": "Validation preview",
    "review.primaryValidate": "Validate packet",
    "review.primaryApprove": "Approve and keep ready to post",
    "review.primaryReject": "Reject invoice",
    "review.applySuccess": "Decision applied.",
    "review.fx": "FX rate",
    "review.fxHint": "FX is written through a separate endpoint and refreshes the work item.",
    "review.fxApply": "Apply FX rate",
    "review.fxApplied": "FX rate updated. The work item was refreshed.",
    "review.fxDirty": "Changing FX makes validation stale.",
    "review.unsupported": "This invoice cannot be completed from this screen.",
    "review.future": "This future-dated transaction can only be posted on or after {date}.",
    "review.postingReady": "Reviewed · Can post now",
    "review.postingLater": "Reviewed · Can post on {date}",
    "review.postingBlocked": "Reviewed · Cannot post",
    "review.needsReview": "This invoice still needs a guided tax decision",
    "review.unknownSupport": "The tax code is not supported for posting.",
    "review.outcomeApprove": "Approve",
    "review.outcomeReject": "Reject",
    "review.actionResolve": "Resolve issue",
    "review.actionKeepOpen": "Keep open",
    "review.documentLink": "Open file",
    "review.invoiceLabel": "Invoice {number}",
    "review.issueOpen": "Open issue",
    "review.issueResolved": "Issue will be resolved",
    "review.assetDecision": "Expense classification",
    "review.assetCurrentExpense": "Current expense",
    "review.assetAsset": "Asset",
    "review.assetNotApplicable": "Not applicable",
    "review.supportReason": "Blocking reason",
    "review.statusReady": "ready",
    "review.statusLater": "later",
    "review.statusBlocked": "blocked",
    "review.stepBusinessPurpose": "Confirm the business purpose of the expense.",
    "review.stepIrpfAmount": "Set the amount deductible for IRPF.",
    "review.stepIvaTreatment": "Verify the VAT treatment and tax base.",
    "review.stepIncomeRecognition": "Confirm the income recognition decision.",
    "review.stepAssetDecision": "Decide whether this is a current expense or an asset.",
    "review.stepBusinessUse": "Set the business-use percentage.",
    "review.stepFx": "Attach an official or settlement EUR rate.",
    "review.stepAmountError": "Resolve the amount mismatch: {detail}.",
    "review.stepUnknown": "Review this requirement manually: {code}.",
    "review.postingTitle": "Post approved transactions",
    "review.postingApproved": "Approved",
    "review.postingBatchReady": "Ready to post",
    "review.postingDeferred": "Deferred",
    "review.postingBatchBlocked": "Blocked",
    "review.postingCleanup": "Cleanup applies",
    "review.postingCleanupBlocked": "Cleanup blocked",
    "review.postingAmount": "Ready amount",
    "review.postingOpenPeriod": "Period is open",
    "review.postingClosedPeriod": "Period is closed",
    "review.postingGeneratedAt": "Generated {date}",
    "review.postingGeneratedUnknown": "Posting preview has no timestamp",
    "review.postingReadyHint": "Only rows that are still ready in the current preview will be posted.",
    "review.postingNothingReady": "There are no rows ready to post right now.",
    "review.postingClosedHint": "Posting is disabled because the selected period is not open.",
    "review.postConfirmTitle": "Post ready transactions",
    "review.postConfirmLead": "Only these rows from the current preview will be posted.",
    "review.postConfirmAction": "Post ready",
    "review.postConfirmCleanupWarning": "Posting affects cleanup: applies {cleanupCount}, blocked {cleanupBlockedCount}. Review the reasons before confirming.",
    "review.postingRows": "Preview rows",
    "review.postingRetryRefresh": "Retry calculation refresh",
    "review.postingStaleWarning": "Transactions were posted, but the tax calculation needs to be refreshed.",
    "review.postingResults": "Posting result",
    "review.postingResultSummary": "Status: {status} · posted {count}",
    "review.postingResultMessage": "Message",
    "review.postSuccess": "Posted: {count}",
    "review.postPartial": "Posting finished partially",
    "review.postInterrupted": "Posting was interrupted",
    "review.postNone": "No new postings were applied",
    "assets.title": "Assets and amortization",
    "assets.asset": "Asset",
    "assets.inService": "In service",
    "assets.cost": "Cost",
    "assets.base": "Base",
    "assets.businessUse": "Business use",
    "assets.rate": "Rate",
    "assets.schedule": "Schedule",
    "assets.decision": "Decision",
    "taxes.obligations": "Obligations",
    "taxes.form": "Form",
    "taxes.applicability": "Applicability",
    "taxes.status": "Status",
    "taxes.directDebit": "Direct debit cutoff",
    "taxes.deadline": "Deadline",
    "taxes.noCalculation": "not calculated",
    "taxes.calculationMissing": "Calculation not available",
    "taxes.filedValuesUnavailable": "The return was filed, but filing-snapshot values are unavailable.",
    "taxes.filedValuesUnavailableExtract": "The return was filed, but values could not be extracted from the filing snapshot.",
    "taxes.filedValuesUnavailablePdf": "The return was filed, but the filing-snapshot PDF could not be read.",
    "contacts.title": "Counterparties",
    "contacts.name": "Name",
    "contacts.country": "Country",
    "contacts.transactions": "Transactions",
    "contacts.last": "Latest",
    "refresh.done": "{period} calculation refreshed",
    "documents.counterparty": "Counterparty",
    "documents.type": "Type",
    "issues.none": "No open issues",
    "issues.sourceDetails": "Source details",
  },
};

const statusMessages = {
  ru: {
    received: "получено",
    extracted: "извлечено",
    needs_review: "нужна проверка",
    approved: "проверено",
    posted: "проведено",
    included_in_snapshot: "в декларации",
    duplicate: "дубликат",
    rejected: "отклонено",
    void: "аннулировано",
    due: "нужно подать",
    not_due: "не применяется",
    unknown: "не определено",
    filed: "подано",
    waived: "не подается",
    blocking: "блокирует",
    warning: "предупреждение",
    high: "высокий",
    medium: "средний",
    low: "низкий",
    error: "ошибка",
    reviewed: "проверено",
    confirmed: "подтверждено",
    pending: "ожидает",
    open: "открыт",
    closed: "закрыт",
    amended: "исправлен",
    deferred: "отложено",
    blocked: "заблокировано",
    partial: "частично",
    interrupted: "прервано",
    completed: "завершено",
    already_posted: "уже проведено",
    already_finalized: "уже включено в декларацию",
    skipped: "пропущено",
    failed: "ошибка",
    not_attempted: "не обработано",
    posted_cleanup_failed: "проведено, очистка не завершена",
    ok: "успешно",
  },
  en: {
    received: "received",
    extracted: "extracted",
    needs_review: "needs review",
    approved: "reviewed",
    posted: "posted",
    included_in_snapshot: "included in filing",
    duplicate: "duplicate",
    rejected: "rejected",
    void: "void",
    due: "due",
    not_due: "not due",
    unknown: "unknown",
    filed: "filed",
    waived: "waived",
    blocking: "blocking",
    warning: "warning",
    high: "high",
    medium: "medium",
    low: "low",
    error: "error",
    reviewed: "reviewed",
    confirmed: "confirmed",
    pending: "pending",
    open: "open",
    closed: "closed",
    amended: "amended",
    deferred: "deferred",
    blocked: "blocked",
    partial: "partial",
    interrupted: "interrupted",
    completed: "completed",
    already_posted: "already posted",
    already_finalized: "already included in filing",
    skipped: "skipped",
    failed: "failed",
    not_attempted: "not attempted",
    posted_cleanup_failed: "posted, cleanup incomplete",
    ok: "ok",
  },
};

const documentTypeMessages = {
  ru: {
    expense_invoice: "счет поставщика",
    income_invoice: "выставленный счет",
    receipt: "чек",
    bank_statement: "банковская выписка",
    tax_report: "налоговый отчет",
    other_document: "другой документ",
  },
  en: {
    expense_invoice: "supplier invoice",
    income_invoice: "issued invoice",
    receipt: "receipt",
    bank_statement: "bank statement",
    tax_report: "tax report",
    other_document: "other document",
  },
};

const casillaMessages = {
  ru: {
    difficult_expenses: "Труднообосновываемые расходы",
    result: "Результат",
    compensation_carryforward: "Перенос компенсации",
  },
  en: {
    difficult_expenses: "Difficult-to-justify expenses",
    result: "Result",
    compensation_carryforward: "Compensation carry-forward",
  },
};

const issueMessages = {
  ru: {
    amount_mismatch: "Сумма не совпадает с источником. Сверьте документ, валюту и налоговую базу.",
    counterparty_tax_profile_review: "Проверьте страну и налоговые реквизиты контрагента перед проведением.",
    deductibility_pending_confirmation: "Нужно подтвердить сумму, допустимую к вычету.",
    document_classification_review: "Документ не удалось надежно классифицировать.",
    foreign_counterparty_identity_missing: "Не хватает подтвержденных налоговых реквизитов иностранного контрагента.",
    missing_locally: "Документ есть в реестре, но локальный оригинал не найден.",
    modelo100_deferral_submission_unconfirmed: "Не подтверждена подача заявления на отсрочку по Modelo 100.",
    modelo130_difficult_expense_filed_method_conflict: "Метод Xolo для 5% труднообосновываемых расходов расходится с инструкцией AEAT.",
    modelo303_q1_notary_vat_base_unverified: "Налоговая база IVA по счету нотариуса требует сверки.",
    modelo303_q1_output_vat_classification_mismatch: "Классификация исходящего IVA не совпадает с поданной Modelo 303.",
    nonresident_payee_legal_form_review: "Нужно уточнить правовую форму иностранного получателя.",
    nonresident_professional_irnr_review: "После оплаты требуется отдельная проверка IRNR по иностранному исполнителю.",
    transaction_tax_review: "Нужно проверить назначение расхода и его учет в IRPF и IVA.",
    default: "Требуется ручная проверка.",
  },
  en: {
    amount_mismatch: "The amount does not match its source. Reconcile the document, currency, and tax base.",
    counterparty_tax_profile_review: "Review the counterparty's country and tax identity before posting.",
    deductibility_pending_confirmation: "Confirm the amount eligible for deduction.",
    document_classification_review: "The document could not be classified reliably.",
    foreign_counterparty_identity_missing: "Verified tax identity is missing for the foreign counterparty.",
    missing_locally: "The register contains this document, but its local original is missing.",
    modelo100_deferral_submission_unconfirmed: "Submission of the Modelo 100 deferral request is not confirmed.",
    modelo130_difficult_expense_filed_method_conflict: "Xolo's treatment of the 5% difficult-to-justify allowance conflicts with AEAT instructions.",
    modelo303_q1_notary_vat_base_unverified: "The VAT base of the notary invoice requires reconciliation.",
    modelo303_q1_output_vat_classification_mismatch: "Output VAT classification does not match the filed Modelo 303.",
    nonresident_payee_legal_form_review: "The foreign payee legal form still needs review.",
    nonresident_professional_irnr_review: "A separate IRNR review is required after payment to the foreign professional.",
    transaction_tax_review: "Review the business purpose and the IRPF and VAT treatment.",
    default: "Manual review is required.",
  },
};

const nounMessages = {
  ru: {
    operations: {one: "операция", few: "операции", many: "операций", other: "операции"},
    steps: {one: "шаг", few: "шага", many: "шагов", other: "шага"},
  },
  en: {
    operations: {one: "transaction", other: "transactions"},
    steps: {one: "step", other: "steps"},
  },
};

const reviewRequirementMessages = {
  confirm_business_purpose: {ru: "review.stepBusinessPurpose", en: "review.stepBusinessPurpose"},
  confirm_irpf_deductible_amount: {ru: "review.stepIrpfAmount", en: "review.stepIrpfAmount"},
  confirm_iva_treatment: {ru: "review.stepIvaTreatment", en: "review.stepIvaTreatment"},
  confirm_income_recognition: {ru: "review.stepIncomeRecognition", en: "review.stepIncomeRecognition"},
  decide_expense_or_amortizable_asset: {ru: "review.stepAssetDecision", en: "review.stepAssetDecision"},
  confirm_business_use_percentage: {ru: "review.stepBusinessUse", en: "review.stepBusinessUse"},
  attach_official_or_settlement_fx: {ru: "review.stepFx", en: "review.stepFx"},
};

const legalFormLabels = {
  unknown: {ru: "неизвестно", en: "unknown"},
  individual: {ru: "физлицо", en: "individual"},
  legal_entity: {ru: "юрлицо", en: "legal entity"},
  public_body: {ru: "госорган", en: "public body"},
};

const roiStatusLabels = {
  unknown: {ru: "не проверено", en: "unknown"},
  registered: {ru: "зарегистрирован", en: "registered"},
  not_registered: {ru: "не зарегистрирован", en: "not registered"},
};

const FORM_KEYS = {
  130: "modelo130",
  303: "modelo303",
};

const state = {
  bootstrap: null,
  period: null,
  view: "dashboard",
  intakeKind: "expense_invoice",
  copyTargetPeriodKey: null,
  copyTargetIsCurrentQuarter: false,
  locale: loadLocale(),
  review: {
    rows: [],
    issues: [],
    documents: [],
    selectedReviewId: null,
    workItem: null,
    validationDirty: true,
    validationResult: null,
    error: "",
    busy: false,
  },
  posting: {
    preview: null,
    previewPeriod: null,
    isSubmitting: false,
    lastResult: null,
    lastResultPeriod: null,
    staleRefresh: null,
    pendingItems: [],
  },
};

const hasDOM = typeof document !== "undefined";
const app = hasDOM ? document.querySelector("#app") : null;
const periodSelect = hasDOM ? document.querySelector("#period-select") : null;
const pageTitle = hasDOM ? document.querySelector("#page-title") : null;
const profileName = hasDOM ? document.querySelector("#profile-name") : null;
const refreshButton = hasDOM ? document.querySelector("#refresh-button") : null;
const newEntryButton = hasDOM ? document.querySelector("#new-entry-button") : null;
const dialog = hasDOM ? document.querySelector("#intake-dialog") : null;
const intakeForm = hasDOM ? document.querySelector("#intake-form") : null;
const intakeKind = hasDOM ? document.querySelector("#intake-kind") : null;
const intakePeriod = hasDOM ? document.querySelector("#intake-period") : null;
const intakeFile = hasDOM ? document.querySelector("#intake-file") : null;
const fileLabel = hasDOM ? document.querySelector("#file-label") : null;
const fileDrop = hasDOM ? document.querySelector("#file-drop") : null;
const intakeNotice = hasDOM ? document.querySelector("#intake-notice") : null;
const intakeStatus = hasDOM ? document.querySelector("#intake-status") : null;
const submitIntake = hasDOM ? document.querySelector("#submit-intake") : null;
const toast = hasDOM ? document.querySelector("#toast") : null;
const localeButtons = hasDOM ? document.querySelectorAll("[data-locale]") : [];
const postingConfirmDialog = hasDOM ? document.querySelector("#posting-confirm-dialog") : null;
const postingConfirmPeriod = hasDOM ? document.querySelector("#posting-confirm-period") : null;
const postingConfirmBody = hasDOM ? document.querySelector("#posting-confirm-body") : null;
const postingConfirmStatus = hasDOM ? document.querySelector("#posting-confirm-status") : null;
const confirmPostingButton = hasDOM ? document.querySelector("#confirm-posting-button") : null;
const incomeCopyRowsById = new Map();
let currentRenderGeneration = 0;

function buildReviewDraftStorageKey(transactionId) {
  return `${REVIEW_DRAFT_STORAGE_PREFIX}:${String(transactionId || "unknown")}`;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function isFutureDateValue(value, today = todayIso()) {
  return Boolean(value) && String(value) > String(today);
}

function reviewIdFromTransaction(transactionId) {
  return `transaction:${transactionId}`;
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function setByPath(target, path, value) {
  const segments = String(path).split(".").map((segment) => (segment.match(/^\d+$/) ? Number(segment) : segment));
  let cursor = target;
  for (let index = 0; index < segments.length - 1; index += 1) {
    const segment = segments[index];
    const nextSegment = segments[index + 1];
    if (cursor[segment] === undefined || cursor[segment] === null) {
      cursor[segment] = typeof nextSegment === "number" ? [] : {};
    }
    cursor = cursor[segment];
  }
  cursor[segments[segments.length - 1]] = value;
}

function deepMerge(base, override) {
  if (Array.isArray(override)) return deepClone(override);
  if (!override || typeof override !== "object") return override;
  const result = Array.isArray(base) ? [] : {...(base || {})};
  Object.entries(override).forEach(([key, value]) => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      result[key] = deepMerge(base?.[key] || {}, value);
      return;
    }
    result[key] = deepClone(value);
  });
  return result;
}

function pickReviewFactDecision(decision) {
  return {
    counterparty_changes: deepClone(decision?.counterparty_changes || {}),
  };
}

function loadReviewDraft(transactionId) {
  if (!transactionId) return null;
  try {
    const raw = localStorage.getItem(buildReviewDraftStorageKey(transactionId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function persistReviewDraft(packet, options = {}) {
  const transactionId = packet?.state?.transaction?.transaction_id;
  if (!transactionId) return;
  const decision = options.factsOnly
    ? pickReviewFactDecision(packet.decision)
    : deepClone(packet.decision);
  const payload = {
    review_id: packet.review_id,
    transaction_id: transactionId,
    snapshot_hash: packet.snapshot_hash,
    decision,
  };
  try {
    localStorage.setItem(buildReviewDraftStorageKey(transactionId), JSON.stringify(payload));
  } catch {
    // Draft persistence is optional.
  }
}

function clearReviewDraft(transactionId) {
  if (!transactionId) return;
  try {
    localStorage.removeItem(buildReviewDraftStorageKey(transactionId));
  } catch {
    // Storage may be unavailable.
  }
}

function mergeReviewDecisionFromDraft(packet, draft, mode = "full") {
  if (!draft?.decision) return packet;
  const nextPacket = deepClone(packet);
  const decisionPatch = mode === "facts" ? pickReviewFactDecision(draft.decision) : draft.decision;
  nextPacket.decision = deepMerge(nextPacket.decision, decisionPatch);
  return nextPacket;
}

function normalizeReviewRequirement(requirement) {
  if (typeof requirement === "string") return {code: requirement, supported: true};
  return {
    ...requirement,
    code: String(requirement?.code || "unknown_requirement"),
    supported: requirement?.supported !== false,
  };
}

function requirementMessageKey(code) {
  if (String(code).startsWith("resolve_amount_error:")) return null;
  return reviewRequirementMessages[code]?.[state.locale] || null;
}

function buildRequirementSteps(requirements) {
  return (requirements || []).map((row, index) => {
    const requirement = normalizeReviewRequirement(row);
    const detail = requirement.code.split(":").slice(1).join(":");
    const messageKey = requirementMessageKey(requirement.code);
    const message = messageKey
      ? t(messageKey)
      : requirement.code.startsWith("resolve_amount_error:")
        ? t("review.stepAmountError", {detail})
        : t("review.stepUnknown", {code: requirement.code});
    return {
      code: requirement.code,
      supported: requirement.supported !== false,
      label: message,
      stepNumber: index + 1,
      description: requirement.description || requirement.message || null,
    };
  });
}

function evaluateWorkItemPosting(workItem, today = todayIso()) {
  const packet = workItem?.packet || {};
  const transaction = packet.state?.transaction || {};
  const requirements = (workItem?.requirements || []).map(normalizeReviewRequirement);
  const unsupportedRequirement = requirements.find((row) => row.supported === false);
  const unavailableReason = workItem?.unavailable_reason || (unsupportedRequirement?.reason || unsupportedRequirement?.message) || null;

  if (isFutureDateValue(transaction.transaction_date, today)) {
    return {
      category: "later",
      supported: false,
      canApply: false,
      availableOn: transaction.transaction_date,
      reason: null,
    };
  }
  if (workItem?.supported === false || unavailableReason) {
    return {
      category: "blocked",
      supported: false,
      canApply: false,
      availableOn: null,
      reason: unavailableReason || t("review.unsupported"),
    };
  }
  if (unsupportedRequirement) {
    return {
      category: "blocked",
      supported: false,
      canApply: false,
      availableOn: null,
      reason: unsupportedRequirement.reason || unsupportedRequirement.message || unsupportedRequirement.code,
    };
  }
  if (transaction.lifecycle_status !== "approved") {
    return {
      category: "needs_review",
      supported: true,
      canApply: true,
      availableOn: null,
      reason: null,
    };
  }
  if (packet.decision?.tax_treatment?.tax_code === "unknown") {
    return {
      category: "blocked",
      supported: false,
      canApply: false,
      availableOn: null,
      reason: t("review.unknownSupport"),
    };
  }
  return {
    category: "ready",
    supported: true,
    canApply: true,
    availableOn: null,
    reason: null,
  };
}

function postingStatusLabelForWorkItem(workItem, locale = state.locale, today = todayIso()) {
  const previousLocale = state.locale;
  state.locale = locale;
  const evaluation = evaluateWorkItemPosting(workItem, today);
  let result;
  if (evaluation.category === "later") {
    result = t("review.postingLater", {date: formatDate(evaluation.availableOn)});
  } else if (evaluation.category === "blocked") {
    result = t("review.postingBlocked");
  } else if (evaluation.category === "needs_review") {
    result = t("review.needsReview");
  } else {
    result = t("review.postingReady");
  }
  state.locale = previousLocale;
  return result;
}

function summarizeReviewRows(rows, today = todayIso()) {
  return rows.reduce((summary, row) => {
    if (row.lifecycle_status !== "approved") {
      summary.needsReview += 1;
      return summary;
    }
    const isBlocked = Number(row.open_issue_count || 0) > 0
      || !row.tax_code
      || row.tax_code === "unknown"
      || ![null, undefined, "approved", "posted", "included_in_snapshot"].includes(row.document_status)
      || ((row.currency || "EUR").toUpperCase() !== "EUR" && !row.amount_eur);
    if (isBlocked) {
      summary.blocked += 1;
    } else if (isFutureDateValue(row.transaction_date, today)) {
      summary.later += 1;
    } else {
      summary.ready += 1;
    }
    return summary;
  }, {needsReview: 0, ready: 0, later: 0, blocked: 0});
}

function formatReviewRowPostingStatus(row, today = todayIso()) {
  if (row.lifecycle_status === "approved") {
    return isFutureDateValue(row.transaction_date, today)
      ? t("review.postingLater", {date: formatDate(row.transaction_date)})
      : t("review.postingReady");
  }
  return t("review.needsReview");
}

function issueBadge(row) {
  if (!row?.open_issue_count) return "";
  return badge(t("transactions.actionNeeded"), "blocking");
}

function readDecisionFieldValue(element) {
  const type = element.dataset.valueType || "string";
  if (element.type === "checkbox") return Boolean(element.checked);
  const raw = element.value;
  if (raw === "") return type.startsWith("nullable-") ? null : raw;
  if (type === "integer") return Number.parseInt(raw, 10);
  if (type === "number") return Number(raw);
  if (type === "boolean" || type === "nullable-boolean") return raw === "true";
  return raw;
}

function updateReviewDecision(path, value) {
  if (!state.review.workItem?.packet) return;
  setByPath(state.review.workItem.packet.decision, path, value);
  state.review.validationDirty = true;
  state.review.validationResult = null;
  state.review.error = "";
  persistReviewDraft(state.review.workItem.packet);
}

function markReviewValidationPending() {
  const primaryButton = document.querySelector("#review-primary-button");
  if (primaryButton) primaryButton.textContent = t("review.primaryValidate");
  const resultStack = document.querySelector(".review-result-stack");
  const alert = resultStack?.querySelector(".review-alert");
  if (alert) {
    alert.className = "review-alert info";
    alert.innerHTML = `
      <strong>${escapeHtml(t("review.validationPending"))}</strong>
      <p>${escapeHtml(t("review.validationPending"))}</p>`;
  }
  resultStack?.querySelector(".review-preview")?.remove();
  const status = resultStack?.closest(".review-panel")?.querySelector(".panel-header small");
  if (status) status.textContent = t("review.validationPending");
}

function currentReviewPacket() {
  return state.review.workItem?.packet || null;
}

function currentReviewTransactionId() {
  return currentReviewPacket()?.state?.transaction?.transaction_id || null;
}

function currentReviewSignature() {
  const packet = currentReviewPacket();
  if (!packet) return null;
  return JSON.stringify(packet.decision);
}

async function changeLocale(locale) {
  if (!SUPPORTED_LOCALES.has(locale)) return;
  const changed = state.locale !== locale;
  state.locale = locale;
  storeLocale(locale);
  if (hasDOM) {
    applyStaticTranslations();
    if (changed && state.period) await renderCurrentView();
  }
}

function t(key, variables = {}) {
  const template = messages[state.locale][key] || messages.ru[key] || key;
  return Object.entries(variables).reduce(
    (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
    template
  );
}

function loadLocale() {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (SUPPORTED_LOCALES.has(stored)) return stored;
  } catch {
    // The UI remains usable when browser storage is disabled.
  }
  return "ru";
}

function storeLocale(locale) {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Locale still applies for the current page when storage is unavailable.
  }
}

function intlLocale() {
  return state.locale === "en" ? "en-GB" : "ru-RU";
}

function countNoun(value, noun) {
  const count = Number(value) || 0;
  const category = new Intl.PluralRules(intlLocale()).select(count);
  const forms = nounMessages[state.locale][noun];
  const label = forms[category] || forms.other;
  return `${count} ${label}`;
}

function waitingForPeriod(value) {
  const count = Number(value) || 0;
  const category = new Intl.PluralRules(intlLocale()).select(count);
  const key = category === "one"
    ? "dashboard.waitingForPeriodOne"
    : "dashboard.waitingForPeriodOther";
  return t(key, {count});
}

function obligationMap(obligations) {
  const map = {};
  (obligations || []).forEach((row) => {
    map[Number(row?.obligation_code)] = row;
  });
  return map;
}

function formCardData(form, obligation, {warnOnMissingHeadline = false} = {}) {
  const fallback = form || {
    form_code: "",
    display_state: "unavailable",
    filed_on: null,
    values: {},
    headline_value: null,
    headline_detail: null,
    preview_as_of: null,
    extraction_status: null,
  };
  const zeroSafeHeadline = fallback.headline_value !== null && fallback.headline_value !== undefined;
  const needsUnavailableWarning = warnOnMissingHeadline
    && !zeroSafeHeadline
    && fallback.display_state === "filed";
  return {
    ...fallback,
    display_state: needsUnavailableWarning ? "filed_without_values" : fallback.display_state,
    obligationFiled: obligation?.filing_status === "filed",
  };
}

function formCardValue(form) {
  return form.headline_value !== null && form.headline_value !== undefined
    ? eur(form.headline_value)
    : "—";
}

function formAccentClass(form) {
  return ["filed", "snapshot_only"].includes(form.display_state) ? "" : "warning";
}

function formSubtitle(form, obligation) {
  const date = form.filed_on ? formatDate(form.filed_on) : null;
  const previewDate = form.preview_as_of ? formatDate(form.preview_as_of) : null;
  let stateText = "";
  if (form.display_state === "filed") {
    stateText = date ? t("dashboard.filedOn", {date}) : t("dashboard.filed");
  } else if (form.display_state === "filed_without_values") {
    const filedLabel = date ? t("dashboard.filedOn", {date}) : t("dashboard.filed");
    stateText = `${filedLabel} · ${t("dashboard.valuesUnavailable")}`;
  } else if (form.display_state === "snapshot_only") {
    stateText = t("dashboard.snapshotAvailable");
  } else if (form.display_state === "preview") {
    stateText = previewDate ? t("dashboard.calculatedAsOf", {date: previewDate}) : t("dashboard.notCalculated");
  } else {
    stateText = t("dashboard.notCalculated");
  }
  if (obligation?.filing_status === "filed" && ["preview", "unavailable"].includes(form.display_state)) {
    stateText = `${t("dashboard.filed")} · ${stateText}`;
  }
  if (form.headline_detail !== null && form.headline_detail !== undefined && !["filed_without_values", "unavailable"].includes(form.display_state)) {
    return `${stateText} · ${t("dashboard.carryForward", {amount: eur(form.headline_detail)})}`;
  }
  return stateText;
}

function showApprovedActivityBanner(actual, forecast) {
  const hasApprovedOnly = Number(actual?.income_transaction_count) === 0
    && Number(actual?.expense_transaction_count) === 0
    && Number(forecast?.transaction_count) > 0;
  if (!hasApprovedOnly) return "";
  return `<div class="period-note">${escapeHtml(t("dashboard.approvedNotPosted"))}</div>`;
}

function formEmptyState(form) {
  if (form.display_state === "filed_without_values") {
    if (form.extraction_status === "values_unavailable") return t("taxes.filedValuesUnavailableExtract");
    if (form.extraction_status === "pdf_unreadable") return t("taxes.filedValuesUnavailablePdf");
    return t("taxes.filedValuesUnavailable");
  }
  return t("taxes.calculationMissing");
}

function statusLabel(value) {
  return statusMessages[state.locale][value] || value.replaceAll("_", " ");
}

function documentTypeLabel(value) {
  const text = String(value || "other_document");
  return documentTypeMessages[state.locale][text] || text.replaceAll("_", " ");
}

function casillaLabel(value) {
  return casillaMessages[state.locale][value] || value;
}

function issueMessage(issueCode) {
  return issueMessages[state.locale][issueCode] || issueMessages[state.locale].default;
}

function eur(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat(intlLocale(), {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
  }).format(number);
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${value}T12:00:00`);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat(intlLocale(), {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

function formatDateTime(value) {
  if (!value) return "—";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return formatDate(text);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat(intlLocale(), {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function boolText(value) {
  return value ? t("common.yes") : t("common.no");
}

async function fetchJSON(url, options = {}) {
  const {fallbackMessage, ...fetchOptions} = options;
  const response = await fetch(url, fetchOptions);
  const responseUrl = new URL(url, window.location.origin);
  const contentType = (response.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  const body = await response.text();
  const trimmedBody = body.trim();
  const parsed = parseJSONText(trimmedBody);

  if (parsed.ok) {
    if (!response.ok) throw new Error(parsed.value?.error || fallbackMessage || `HTTP ${response.status}`);
    return parsed.value;
  }

  if (response.ok && looksLikeHtmlResponse(contentType, trimmedBody)) {
    throw new Error(t("errors.apiReturnedHtml", {origin: responseUrl.origin}));
  }

  const messageKey = looksLikeJsonResponse(contentType, trimmedBody)
    ? "errors.malformedJson"
    : "errors.unexpectedNonJson";
  throw new Error(t(messageKey, {url: responseUrl.href, status: response.status}));
}

function parseJSONText(body) {
  if (!body) return {ok: false};
  try {
    return {ok: true, value: JSON.parse(body)};
  } catch {
    return {ok: false};
  }
}

function looksLikeHtmlResponse(contentType, body) {
  return contentType === "text/html"
    || contentType === "application/xhtml+xml"
    || /^<!doctype html\b/i.test(body)
    || /^<html\b/i.test(body)
    || /^<head\b/i.test(body)
    || /^<body\b/i.test(body);
}

function looksLikeJsonResponse(contentType, body) {
  return contentType === "application/json"
    || contentType.endsWith("+json")
    || /^[\[{]/.test(body);
}

function showToast(message, error = false) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("visible"), 3200);
}

function emptyRow(columns) {
  return `<tr><td colspan="${columns}"><div class="empty-state">${escapeHtml(t("common.noRecords"))}</div></td></tr>`;
}

function errorState(error) {
  return `<div class="empty-state">${escapeHtml(error.message || String(error))}</div>`;
}

function shortId(value) {
  return value ? String(value).slice(0, 8) : t("common.noId");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function debounce(callback, wait) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => callback(...args), wait);
  };
}

function badge(value, forcedClass) {
  const text = String(value ?? "unknown");
  const className = forcedClass || text.replace(/[^a-z0-9_-]/gi, "_");
  return `<span class="badge ${escapeHtml(className)}">${escapeHtml(statusLabel(text))}</span>`;
}

function metric(label, value, detail, className = "") {
  return `
    <div class="metric ${className}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(detail)}</small>
    </div>`;
}

function casillas(values, keys, emptyMessage = t("taxes.calculationMissing")) {
  if (!Object.keys(values).length) {
    return `<div class="empty-state">${escapeHtml(emptyMessage)}</div>`;
  }
  return `
    <div class="casilla-grid">
      ${keys.filter((key) => values[key] !== undefined).map((key) => `
        <div class="casilla"><span>${escapeHtml(casillaLabel(key))}</span><strong>${eur(values[key])}</strong></div>
      `).join("")}
    </div>`;
}

function transactionTable(rows, {copyable = false} = {}) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("transactions.status"))}</th><th>${escapeHtml(t("transactions.amount"))}</th><th>${escapeHtml(t("transactions.irpfDeduction"))}</th><th>IVA</th><th></th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${formatDate(row.transaction_date)}</td>
              <td class="cell-primary">
                <strong>${escapeHtml(row.counterparty_name || row.description || t("transactions.noCounterparty"))}</strong>
                <small>${escapeHtml(row.document_number || row.description || "")}</small>
              </td>
              <td>${badge(row.lifecycle_status)}${row.open_issue_count ? ` ${issueBadge(row)}` : ""}</td>
              <td class="amount">${row.amount_eur ? eur(row.amount_eur) : `${escapeHtml(row.amount_original || "—")} ${escapeHtml(row.currency || "")}`}</td>
              <td class="amount">${row.deductible_irpf_eur ? eur(row.deductible_irpf_eur) : "—"}</td>
              <td class="amount">${row.deductible_vat_eur ? eur(row.deductible_vat_eur) : "—"}</td>
              <td>${transactionActions(row, copyable)}</td>
            </tr>`).join("") || emptyRow(7)}
        </tbody>
      </table>
    </div>`;
}

function transactionActions(row, copyable) {
  const actions = [];
  if (row.document_id) {
    actions.push(`<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("common.file"))}</a>`);
  }
  if (isCopyableIncomeRow(row, {copyable})) {
    actions.push(copyTransactionAction(row.transaction_id));
  }
  return actions.length ? `<div class="transaction-actions">${actions.join("")}</div>` : "";
}

function documentTable(rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("documents.counterparty"))}</th><th>${escapeHtml(t("fields.number"))}</th><th>${escapeHtml(t("documents.type"))}</th><th>${escapeHtml(t("transactions.status"))}</th><th>${escapeHtml(t("transactions.amount"))}</th><th></th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${formatDate(row.issued_on)}</td>
              <td>${escapeHtml(row.counterparty_name || "—")}</td>
              <td>${escapeHtml(row.document_number || "—")}</td>
              <td>${escapeHtml(documentTypeLabel(row.document_type))}</td>
              <td>${badge(row.lifecycle_status)}${row.open_issue_count ? ` ${issueBadge(row)}` : ""}</td>
              <td class="amount">${row.total_eur ? eur(row.total_eur) : "—"}</td>
              <td>${row.source_available ? `<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("common.file"))}</a>` : ""}</td>
            </tr>`).join("") || emptyRow(7)}
        </tbody>
      </table>
    </div>`;
}

function issuesList(rows) {
  if (!rows.length) return `<div class="empty-state">${escapeHtml(t("issues.none"))}</div>`;
  return `
    <ul class="issues-list">
      ${rows.map((row) => `
        <li>
          <strong>${badge(row.blocking ? "blocking" : row.severity)} ${escapeHtml(issueMessage(row.issue_code))}</strong>
          <details class="issue-details">
            <summary>${escapeHtml(t("issues.sourceDetails"))}</summary>
            <code>${escapeHtml(row.issue_code)}</code>
            <span>${escapeHtml(row.message)}</span>
          </details>
        </li>`).join("")}
    </ul>`;
}

async function init() {
  try {
    state.bootstrap = await fetchJSON("/api/bootstrap");
    profileName.textContent = state.bootstrap.profile_name;
    state.period = state.bootstrap.default_period;
    refreshCopyTargetState();
    periodSelect.innerHTML = state.bootstrap.periods
      .map((period) => `<option value="${escapeHtml(period.period_key)}">${escapeHtml(period.period_key)}</option>`)
      .join("");
    periodSelect.value = state.period;
    newEntryButton.disabled = !state.bootstrap.intake_enabled;
    await renderCurrentView();
  } catch (error) {
    app.innerHTML = errorState(error);
  }
}

async function renderCurrentView() {
  if (!state.period || !app) return;
  const renderGeneration = ++currentRenderGeneration;
  refreshCopyTargetState();
  incomeCopyRowsById.clear();
  if (state.view !== "review") closePostingConfirmDialog();
  app.innerHTML = `<div class="loading-state">${escapeHtml(t("common.loading"))}</div>`;
  try {
    if (state.view === "dashboard") await renderDashboard(renderGeneration);
    if (state.view === "income") await renderTransactions("income", renderGeneration);
    if (state.view === "expenses") await renderTransactions("expense", renderGeneration);
    if (state.view === "review") await renderReview(renderGeneration);
    if (state.view === "assets") await renderAssets();
    if (state.view === "taxes") await renderTaxes();
    if (state.view === "contacts") await renderContacts();
  } catch (error) {
    app.innerHTML = errorState(error);
  }
}

async function renderDashboard(renderGeneration = currentRenderGeneration) {
  const renderToken = transactionRenderToken("dashboard", renderGeneration);
  const data = await fetchJSON(`/api/dashboard?period=${encodeURIComponent(state.period)}`);
  if (!isActiveTransactionRenderToken(renderToken, "dashboard", renderGeneration)) return;
  replaceIncomeCopyRows(data.recent_transactions || []);
  const actual = data.totals.actual;
  const forecast = data.totals.forecast;
  const obligations = obligationMap(data.obligations);
  const postingSummary = normalizePostingSummary(data.posting_preview_summary || data.summary);
  const m130 = formCardData(data.tax_forms?.[FORM_KEYS[130]], obligations[130], {warnOnMissingHeadline: true});
  const m303 = formCardData(data.tax_forms?.[FORM_KEYS[303]], obligations[303], {warnOnMissingHeadline: true});
  const nextDue = data.obligations.find((item) => item.determination === "due" && item.filing_status !== "filed");
  const readyCount = Number(data.readyCount ?? data.ready_count ?? forecast.transaction_count ?? 0);
  const readyKey = readyCount === 1 ? "dashboard.readyBannerOne" : "dashboard.readyBannerOther";

  app.innerHTML = `
    ${showApprovedActivityBanner(postingSummary)}
    <div class="metric-grid">
      ${metric(t("dashboard.incomePosted"), eur(actual.income_eur), countNoun(actual.income_transaction_count, "operations"), "accent")}
      ${metric(t("dashboard.expensesPosted"), eur(actual.expense_gross_eur), `IRPF ${eur(actual.deductible_irpf_eur)}`)}
      ${metric(t("dashboard.expensesForecast"), eur(forecast.expense_gross_eur), countNoun(forecast.transaction_count, "operations"), "warning")}
      ${metric(t("dashboard.modelo130Box"), formCardValue(m130), formSubtitle(m130, obligations[130]), formAccentClass(m130))}
      ${metric(t("dashboard.modelo303Result"), formCardValue(m303), formSubtitle(m303, obligations[303]), formAccentClass(m303))}
    </div>
    ${readyCount > 0 ? `
      <button class="review-banner" type="button" id="dashboard-ready-banner">
        <strong>${escapeHtml(t(readyKey, {count: readyCount}))}</strong>
        <span>${escapeHtml(t("dashboard.readyBannerAction"))}</span>
      </button>` : ""}
    ${nextDue ? `
      <div class="deadline-strip">
        <strong>Modelo ${escapeHtml(nextDue.obligation_code)}</strong>
        <p>${escapeHtml(t("dashboard.obligationDue"))}</p>
        <time datetime="${escapeHtml(nextDue.statutory_due_on || "")}">${formatDate(nextDue.statutory_due_on)}</time>
      </div>` : ""}
    <div class="dashboard-grid">
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("dashboard.recentTransactions"))}</h2><small>${escapeHtml(state.period)}</small></header>
        ${transactionTable(data.recent_transactions, {copyable: Boolean(state.copyTargetPeriodKey)})}
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("dashboard.needsAttention"))}</h2><small>${data.open_issues.length}</small></header>
        ${issuesList(data.open_issues)}
      </section>
    </div>
  `;
  document.querySelector("#dashboard-ready-banner")?.addEventListener("click", async () => {
    state.view = "review";
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.view === "review");
    });
    pageTitle.textContent = t("titles.review");
    await renderCurrentView();
  });
}

async function renderTransactions(entryType, renderGeneration = currentRenderGeneration) {
  const renderToken = transactionRenderToken(entryType, renderGeneration);
  let latestSearchRequestId = 0;
  const rows = await fetchJSON(
    `/api/transactions?period=${encodeURIComponent(state.period)}&entry_type=${entryType}`
  );
  if (!isActiveTransactionRenderToken(renderToken, entryType, renderGeneration)) return;
  if (entryType === "income") replaceIncomeCopyRows(rows);
  const label = entryType === "income" ? t("titles.income") : t("titles.expenses");
  app.innerHTML = `
    <div class="table-toolbar">
      <h2>${label} · ${escapeHtml(state.period)}</h2>
      <div class="toolbar-filters">
        <input id="transaction-search" type="search" placeholder="${escapeHtml(t("transactions.search"))}">
        <button class="primary-button" id="view-add-entry"><span aria-hidden="true">+</span> ${escapeHtml(t("common.add"))}</button>
      </div>
    </div>
    <section class="panel">
      <div id="transactions-table">${transactionTable(rows, {copyable: entryType === "income" && Boolean(state.copyTargetPeriodKey)})}</div>
    </section>
  `;
  document.querySelector("#view-add-entry").addEventListener("click", () => {
    openIntake(entryType === "income" ? "income_invoice" : "expense_invoice");
  });
  const search = document.querySelector("#transaction-search");
  const searchToken = renderToken;
  search.addEventListener("input", debounce(async () => {
    const searchRequestId = ++latestSearchRequestId;
    const query = search.value.trim();
    const url = `/api/transactions?period=${encodeURIComponent(state.period)}&entry_type=${entryType}&q=${encodeURIComponent(query)}`;
    const filtered = await fetchJSON(url);
    if (!isActiveTransactionRenderToken(searchToken, entryType, renderGeneration)) return;
    if (searchRequestId !== latestSearchRequestId) return;
    const table = document.querySelector("#transactions-table");
    if (!table) return;
    if (entryType === "income") replaceIncomeCopyRows(filtered);
    table.innerHTML = transactionTable(
      filtered,
      {copyable: entryType === "income" && Boolean(state.copyTargetPeriodKey)}
    );
  }, 240));
}

async function fetchReviewWorkItem(reviewId, options = {}) {
  const workItem = await fetchJSON(`/api/review/work-item?review_id=${encodeURIComponent(reviewId)}`);
  const packet = deepClone(workItem.packet || {});
  const transactionId = packet.state?.transaction?.transaction_id;
  const draft = loadReviewDraft(transactionId);
  const mode = options.factsOnly ? "facts" : (draft && draft.snapshot_hash === packet.snapshot_hash ? "full" : "facts");
  const mergedPacket = mergeReviewDecisionFromDraft(packet, draft, mode);
  state.review.workItem = {
    ...workItem,
    packet: mergedPacket,
    requirements: (workItem.requirements || []).map(normalizeReviewRequirement),
  };
  state.review.validationDirty = true;
  state.review.validationResult = null;
  state.review.error = "";
  persistReviewDraft(mergedPacket, {factsOnly: options.factsOnly});
}

function renderReviewOverview() {
  const summary = summarizeReviewRows(state.review.rows);
  const preview = currentPostingPreview() || normalizePostingPreview({period: state.period});
  const reviewDocuments = state.review.documents.filter((item) =>
    ["received", "extracted", "needs_review"].includes(item.lifecycle_status)
  );
  app.innerHTML = `
    <div class="section-stack review-shell">
      <section class="panel review-summary-panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.summary"))}</h2><small>${escapeHtml(state.period)}</small></header>
        <div class="review-summary-grid">
          <article class="review-summary-card needs-review">
            <span>${escapeHtml(t("review.summaryNeedsReview"))}</span>
            <strong>${summary.needsReview}</strong>
          </article>
          <article class="review-summary-card ready">
            <span>${escapeHtml(t("review.summaryReady"))}</span>
            <strong>${summary.ready}</strong>
          </article>
          <article class="review-summary-card later">
            <span>${escapeHtml(t("review.summaryLater"))}</span>
            <strong>${summary.later}</strong>
          </article>
          <article class="review-summary-card blocked">
            <span>${escapeHtml(t("review.summaryBlocked"))}</span>
            <strong>${summary.blocked}</strong>
          </article>
        </div>
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.postingTitle"))}</h2><small>${escapeHtml(state.period)}</small></header>
        <div class="posting-panel">
          ${renderPostingRefreshWarning()}
          ${renderPostingPeriodWarning(preview)}
          <div class="posting-summary-grid">
            ${postingStat(t("review.postingApproved"), preview.summary.approvedCount)}
            ${postingStat(t("review.postingBatchReady"), preview.summary.readyCount, eur(preview.summary.readyTotalEur))}
            ${postingStat(t("review.postingDeferred"), preview.summary.deferredCount)}
            ${postingStat(t("review.postingBatchBlocked"), preview.summary.blockedCount)}
            ${postingStat(t("review.postingCleanup"), preview.summary.cleanupCount)}
            ${postingStat(t("review.postingCleanupBlocked"), preview.summary.cleanupBlockedCount)}
          </div>
          <div class="posting-action-row">
            <div class="posting-meta">
              <strong>${escapeHtml(preview.periodStatus === "open" ? t("review.postingOpenPeriod") : t("review.postingClosedPeriod"))}</strong>
              <span>${escapeHtml(postingPreviewTimestamp(preview))}</span>
            </div>
            <button
              type="button"
              class="primary-button"
              data-posting-action="open-confirm"
              ${postingActionDisabled(preview) ? "disabled" : ""}
            >${escapeHtml(t("review.postConfirmAction"))}</button>
          </div>
          <p class="posting-action-hint">${escapeHtml(postingActionHint(preview))}</p>
          ${renderPostingQueueSection(t("review.postingDeferred"), preview.deferred)}
          ${renderPostingQueueSection(t("review.postingBatchBlocked"), preview.blocked)}
          ${renderPostingResultSection()}
        </div>
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.transactions"))}</h2><small>${state.review.rows.length}</small></header>
        ${reviewTransactionTable(state.review.rows)}
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.documents"))}</h2><small>${reviewDocuments.length}</small></header>
        ${documentTable(reviewDocuments)}
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.openIssues"))}</h2><small>${state.review.issues.length}</small></header>
        ${issuesList(state.review.issues)}
      </section>
    </div>
  `;
  document.querySelectorAll("[data-open-review-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.review.selectedReviewId = button.dataset.openReviewId;
      app.innerHTML = `<div class="loading-state">${escapeHtml(t("common.loading"))}</div>`;
      try {
        await fetchReviewWorkItem(state.review.selectedReviewId);
        renderReviewWorkspace();
      } catch (error) {
        state.review.selectedReviewId = null;
        app.innerHTML = errorState(error);
      }
    });
  });
}

function reviewTransactionTable(rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("review.taxDecision"))}</th><th>${escapeHtml(t("review.result"))}</th><th>${escapeHtml(t("transactions.amount"))}</th><th></th></tr></thead>
        <tbody>
          ${rows.map((row) => {
            const reviewId = reviewIdFromTransaction(row.transaction_id);
            const canOpenWorkspace = row.lifecycle_status === "needs_review";
            return `
              <tr>
                <td>${formatDate(row.transaction_date)}</td>
                <td class="cell-primary">
                  <strong>${escapeHtml(row.counterparty_name || row.description || t("transactions.noCounterparty"))}</strong>
                  <small>${escapeHtml(row.document_number || row.description || "")}</small>
                </td>
                <td>
                  ${badge(row.lifecycle_status)}
                  ${row.open_issue_count ? `<div class="inline-meta">${issueBadge(row)}</div>` : ""}
                </td>
                <td class="cell-primary">
                  <strong>${escapeHtml(formatReviewRowPostingStatus(row))}</strong>
                  <small>${escapeHtml(row.tax_code || "—")}</small>
                </td>
                <td class="amount">${row.amount_eur ? eur(row.amount_eur) : `${escapeHtml(row.amount_original || "—")} ${escapeHtml(row.currency || "")}`}</td>
                <td class="table-actions">
                  ${row.document_id ? `<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("review.documentLink"))}</a>` : ""}
                  ${canOpenWorkspace ? `<button type="button" class="secondary-button compact-button" data-open-review-id="${escapeHtml(reviewId)}">${escapeHtml(t("review.openWorkspace"))}</button>` : ""}
                </td>
              </tr>`;
          }).join("") || emptyRow(6)}
        </tbody>
      </table>
    </div>`;
}

async function renderReview(renderGeneration = currentRenderGeneration) {
  const [rows, issues, documents, previewPayload] = await Promise.all([
    fetchJSON(`/api/transactions?period=${encodeURIComponent(state.period)}&status=review`),
    fetchJSON(`/api/issues?period=${encodeURIComponent(state.period)}`),
    fetchJSON(`/api/documents?period=${encodeURIComponent(state.period)}`),
    fetchJSON(`/api/review/posting-preview?period=${encodeURIComponent(state.period)}`),
  ]);
  if (renderGeneration !== currentRenderGeneration || state.view !== "review") return;
  state.review.rows = rows;
  state.review.issues = issues;
  state.review.documents = documents;
  state.posting.preview = normalizePostingPreview(previewPayload);
  state.posting.previewPeriod = state.period;
  if (state.review.selectedReviewId) {
    if (!state.review.workItem || state.review.workItem.packet?.review_id !== state.review.selectedReviewId) {
      await fetchReviewWorkItem(state.review.selectedReviewId);
    }
    renderReviewWorkspace();
    return;
  }
  renderReviewOverview();
}

function reviewSelectOptions(values, selectedValue, includeBlank = false) {
  const options = [];
  if (includeBlank) {
    options.push(`<option value=""></option>`);
  }
  values.forEach((value) => {
    options.push(`<option value="${escapeHtml(value)}"${value === selectedValue ? " selected" : ""}>${escapeHtml(value)}</option>`);
  });
  return options.join("");
}

function renderReviewRequirements(requirements) {
  const steps = buildRequirementSteps(requirements);
  return `
    <ol class="review-steps">
      ${steps.map((step) => `
        <li class="review-step ${step.supported ? "supported" : "unsupported"}">
          <div>
            <strong>${escapeHtml(t("common.step"))} ${step.stepNumber}</strong>
            <p>${escapeHtml(step.label)}</p>
            ${step.description ? `<small>${escapeHtml(step.description)}</small>` : ""}
          </div>
          <span class="review-step-state">${escapeHtml(step.supported ? t("review.requirementSupported") : t("review.requirementUnsupported"))}</span>
        </li>`).join("")}
    </ol>`;
}

function renderReviewIssueResolution(issue, index, resolution) {
  const action = resolution?.action || "";
  const reason = resolution?.reason || "";
  return `
    <article class="review-issue-card">
      <header>
        <strong>${escapeHtml(issueMessage(issue.issue_code))}</strong>
        <span>${escapeHtml(action === "resolve" ? t("review.issueResolved") : t("review.issueOpen"))}</span>
      </header>
      <p>${escapeHtml(issue.message || "")}</p>
      <label>
        <span>${escapeHtml(t("fields.reviewAction"))}</span>
        <select data-decision-path="issue_resolutions.${index}.action" data-value-type="string">
          <option value=""></option>
          <option value="resolve"${action === "resolve" ? " selected" : ""}>${escapeHtml(t("review.actionResolve"))}</option>
          <option value="keep_open"${action === "keep_open" ? " selected" : ""}>${escapeHtml(t("review.actionKeepOpen"))}</option>
        </select>
      </label>
      <label>
        <span>${escapeHtml(t("fields.reviewReason"))}</span>
        <textarea rows="2" data-decision-path="issue_resolutions.${index}.reason">${escapeHtml(reason)}</textarea>
      </label>
    </article>`;
}

function renderReviewWorkspace() {
  const workItem = state.review.workItem;
  if (!workItem?.packet) {
    app.innerHTML = `<div class="empty-state">${escapeHtml(t("common.noRecords"))}</div>`;
    return;
  }
  const packet = workItem.packet;
  const decision = packet.decision;
  const packetState = packet.state || {};
  const transaction = packetState.transaction || {};
  const documentState = packetState.document || {};
  const counterparty = packetState.counterparty || {};
  const issues = packetState.issues || [];
  const fx = packetState.fx || {};
  const evaluation = evaluateWorkItemPosting(workItem);
  const disabledWorkspace = evaluation.category === "later" || evaluation.category === "blocked";
  const validationMessage = state.review.validationResult
    ? t("review.validationPassed")
    : t("review.validationPending");
  const sourceHref = documentState.document_id
    ? `/api/document/${encodeURIComponent(documentState.document_id)}/content`
    : "";

  app.innerHTML = `
    <div class="review-workspace">
      <div class="review-workspace-header">
        <button type="button" class="secondary-button" id="review-back-button">${escapeHtml(t("review.workspaceBack"))}</button>
        <div class="review-workspace-title">
          <h2>${escapeHtml(counterparty.display_name || documentState.document_number || t("review.workspaceTitle"))}</h2>
          <p>${escapeHtml(documentState.document_number ? t("review.invoiceLabel", {number: documentState.document_number}) : t("review.workspaceTitle"))}</p>
        </div>
        <div class="review-workspace-status">
          ${badge(evaluation.category === "blocked" ? "blocking" : evaluation.category)}
          <strong>${escapeHtml(postingStatusLabelForWorkItem(workItem))}</strong>
        </div>
      </div>
      ${evaluation.category === "blocked" ? `
        <div class="review-alert error" role="alert">
          <strong>${escapeHtml(t("review.unsupported"))}</strong>
          <p>${escapeHtml(evaluation.reason || workItem.unavailable_reason || t("review.unknownSupport"))}</p>
        </div>` : ""}
      ${evaluation.category === "later" ? `
        <div class="review-alert warning" role="alert">
          <strong>${escapeHtml(t("review.summaryLater"))}</strong>
          <p>${escapeHtml(t("review.future", {date: formatDate(evaluation.availableOn)}))}</p>
        </div>` : ""}
      ${state.review.error ? `<div class="review-alert error" role="alert">${escapeHtml(state.review.error)}</div>` : ""}
      <form id="review-form" class="review-form">
        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.facts"))}</h2><small>${escapeHtml(t("review.requirements"))}</small></header>
          <div class="review-facts-grid">
            <article class="review-card">
              <h3>${escapeHtml(t("review.requirements"))}</h3>
              ${renderReviewRequirements(workItem.requirements)}
            </article>
            <article class="review-card">
              <h3>${escapeHtml(t("review.counterpartyFacts"))}</h3>
              <p class="muted-copy">${escapeHtml(t("review.counterpartyFactsHint"))}</p>
              <div class="form-grid compact-grid">
                <label>
                  <span>${escapeHtml(t("fields.counterpartyCountry"))}</span>
                  <input type="text" value="${escapeHtml(decision.counterparty_changes.country_code ?? counterparty.country_code ?? "")}" data-decision-path="counterparty_changes.country_code">
                </label>
                <label>
                  <span>${escapeHtml(t("fields.taxId"))}</span>
                  <input type="text" value="${escapeHtml(decision.counterparty_changes.tax_id ?? counterparty.tax_id ?? "")}" data-decision-path="counterparty_changes.tax_id">
                </label>
                <label>
                  <span>${escapeHtml(t("fields.vatId"))}</span>
                  <input type="text" value="${escapeHtml(decision.counterparty_changes.vat_id ?? counterparty.vat_id ?? "")}" data-decision-path="counterparty_changes.vat_id">
                </label>
                <label>
                  <span>${escapeHtml(t("fields.roiStatus"))}</span>
                  <select data-decision-path="counterparty_changes.roi_status">
                    ${reviewSelectOptions(Object.keys(roiStatusLabels), decision.counterparty_changes.roi_status ?? counterparty.roi_status ?? "unknown")}
                  </select>
                </label>
                <label>
                  <span>${escapeHtml(t("fields.legalForm"))}</span>
                  <select data-decision-path="counterparty_changes.legal_form">
                    ${reviewSelectOptions(Object.keys(legalFormLabels), decision.counterparty_changes.legal_form ?? counterparty.legal_form ?? "unknown")}
                  </select>
                </label>
                <label class="checkbox-label">
                  <input type="checkbox" ${Boolean(decision.counterparty_changes.professional_supplier ?? counterparty.professional_supplier) ? "checked" : ""} data-decision-path="counterparty_changes.professional_supplier" data-value-type="boolean">
                  <span>${escapeHtml(t("fields.professionalSupplier"))}</span>
                </label>
                <label class="checkbox-label">
                  <input type="checkbox" ${Boolean(decision.counterparty_changes.retention_expected ?? counterparty.retention_expected) ? "checked" : ""} data-decision-path="counterparty_changes.retention_expected" data-value-type="boolean">
                  <span>${escapeHtml(t("fields.retentionExpected"))}</span>
                </label>
              </div>
            </article>
            <article class="review-card">
              <h3>${escapeHtml(t("review.workspaceTitle"))}</h3>
              <dl class="review-facts-list">
                <div><dt>${escapeHtml(t("fields.transactionDate"))}</dt><dd>${formatDate(transaction.transaction_date)}</dd></div>
                <div><dt>${escapeHtml(t("fields.bookingDate"))}</dt><dd>${formatDate(transaction.booking_date)}</dd></div>
                <div><dt>${escapeHtml(t("fields.currency"))}</dt><dd>${escapeHtml(transaction.currency || documentState.currency || "EUR")}</dd></div>
                <div><dt>${escapeHtml(t("fields.amount"))}</dt><dd>${transaction.amount_eur_minor != null ? eur(transaction.amount_eur_minor / 100) : transaction.amount_minor != null ? `${escapeHtml(new Intl.NumberFormat(intlLocale(), {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(transaction.amount_minor / 100))} ${escapeHtml(transaction.currency || "")}` : "—"}</dd></div>
                <div><dt>${escapeHtml(t("fields.number"))}</dt><dd>${escapeHtml(documentState.document_number || "—")}</dd></div>
                <div><dt>${escapeHtml(t("fields.issuedOn"))}</dt><dd>${formatDate(documentState.issued_on)}</dd></div>
              </dl>
              ${sourceHref ? `<a class="text-button" href="${escapeHtml(sourceHref)}" target="_blank" rel="noreferrer">${escapeHtml(t("review.documentLink"))}</a>` : ""}
            </article>
            ${(transaction.original_currency && transaction.original_currency !== "EUR") || fx.rate ? `
              <article class="review-card">
                <h3>${escapeHtml(t("review.fx"))}</h3>
                <p class="muted-copy">${escapeHtml(t("review.fxHint"))}</p>
                <div class="form-grid compact-grid">
                  <label>
                    <span>${escapeHtml(t("fields.fxRateDate"))}</span>
                    <input type="date" id="review-fx-rate-date" value="${escapeHtml(fx.rate_date || transaction.transaction_date || todayIso())}">
                  </label>
                  <label>
                    <span>${escapeHtml(t("fields.fxRate"))}</span>
                    <input type="number" id="review-fx-rate" min="0" step="0.000001" inputmode="decimal" value="${escapeHtml(fx.rate || "")}">
                  </label>
                  <label>
                    <span>${escapeHtml(t("fields.fxRateSource"))}</span>
                    <select id="review-fx-source">
                      ${reviewSelectOptions(packet.allowed_values?.production_fx_source || [], fx.rate_source || "", true)}
                    </select>
                  </label>
                  <label class="full-span">
                    <span>${escapeHtml(t("fields.fxSourceReference"))}</span>
                    <input type="text" id="review-fx-reference" value="${escapeHtml(fx.source_reference || "")}">
                  </label>
                </div>
                <button type="button" class="secondary-button" id="review-fx-apply">${escapeHtml(t("review.fxApply"))}</button>
              </article>` : ""}
          </div>
        </section>

        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.taxDecision"))}</h2><small>${escapeHtml(transaction.entry_type || "invoice")}</small></header>
          <div class="form-grid review-form-grid">
            <label>
              <span>${escapeHtml(t("fields.outcome"))}</span>
              <select data-decision-path="outcome">
                <option value=""></option>
                <option value="approve"${decision.outcome === "approve" ? " selected" : ""}>${escapeHtml(t("review.outcomeApprove"))}</option>
                <option value="reject"${decision.outcome === "reject" ? " selected" : ""}>${escapeHtml(t("review.outcomeReject"))}</option>
              </select>
            </label>
            <label>
              <span>${escapeHtml(t("fields.documentValid"))}</span>
              <select data-decision-path="document_valid" data-value-type="boolean">
                <option value=""></option>
                <option value="true"${decision.document_valid === true ? " selected" : ""}>${escapeHtml(t("common.yes"))}</option>
                <option value="false"${decision.document_valid === false ? " selected" : ""}>${escapeHtml(t("common.no"))}</option>
              </select>
            </label>
            ${transaction.entry_type === "expense" ? `
              <label>
                <span>${escapeHtml(t("review.assetDecision"))}</span>
                <select data-decision-path="asset_decision">
                  <option value=""></option>
                  <option value="current_expense"${decision.asset_decision === "current_expense" ? " selected" : ""}>${escapeHtml(t("review.assetCurrentExpense"))}</option>
                  <option value="asset"${decision.asset_decision === "asset" ? " selected" : ""}>${escapeHtml(t("review.assetAsset"))}</option>
                </select>
              </label>` : `
              <label>
                <span>${escapeHtml(t("review.assetDecision"))}</span>
                <input type="text" value="${escapeHtml(t("review.assetNotApplicable"))}" disabled>
              </label>`}
            <label class="full-span">
              <span>${escapeHtml(t("fields.reason"))}</span>
              <textarea rows="3" data-decision-path="reason">${escapeHtml(decision.reason || "")}</textarea>
            </label>
            <label class="full-span">
              <span>${escapeHtml(t("fields.businessPurpose"))}</span>
              <textarea rows="3" data-decision-path="business_purpose">${escapeHtml(decision.business_purpose || "")}</textarea>
            </label>
            <label>
              <span>${escapeHtml(t("fields.taxCode"))}</span>
              <select data-decision-path="tax_treatment.tax_code">
                ${reviewSelectOptions(packet.allowed_values?.tax_code || Array.from(KNOWN_REVIEW_TAX_CODES), decision.tax_treatment.tax_code || "", true)}
              </select>
            </label>
            <label>
              <span>${escapeHtml(t("fields.taxableBaseMinor"))}</span>
              <input type="number" inputmode="numeric" data-decision-path="tax_treatment.taxable_base_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.taxable_base_minor ?? "")}">
            </label>
            <label>
              <span>${escapeHtml(t("fields.vatMinor"))}</span>
              <input type="number" inputmode="numeric" data-decision-path="tax_treatment.vat_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.vat_minor ?? "")}">
            </label>
            <label>
              <span>${escapeHtml(t("fields.deductibleIrpfMinor"))}</span>
              <input type="number" inputmode="numeric" data-decision-path="tax_treatment.deductible_irpf_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.deductible_irpf_minor ?? "")}">
            </label>
            <label>
              <span>${escapeHtml(t("fields.deductibleVatMinor"))}</span>
              <input type="number" inputmode="numeric" data-decision-path="tax_treatment.deductible_vat_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.deductible_vat_minor ?? "")}">
            </label>
            <label>
              <span>${escapeHtml(t("fields.withholdingMinor"))}</span>
              <input type="number" inputmode="numeric" data-decision-path="tax_treatment.withholding_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.withholding_minor ?? "")}">
            </label>
            <label>
              <span>${escapeHtml(t("fields.deductibleRatio"))}</span>
              <input type="number" min="0" max="1" step="0.01" inputmode="decimal" data-decision-path="tax_treatment.deductible_ratio" data-value-type="number" value="${escapeHtml(decision.tax_treatment.deductible_ratio ?? "")}">
            </label>
            <label class="checkbox-label">
              <input type="checkbox" ${Boolean(decision.tax_treatment.include_modelo130) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo130" data-value-type="boolean">
              <span>${escapeHtml(t("fields.includeModelo130"))}</span>
            </label>
            <label class="checkbox-label">
              <input type="checkbox" ${Boolean(decision.tax_treatment.include_modelo303) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo303" data-value-type="boolean">
              <span>${escapeHtml(t("fields.includeModelo303"))}</span>
            </label>
            <label class="checkbox-label">
              <input type="checkbox" ${Boolean(decision.tax_treatment.include_modelo347) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo347" data-value-type="boolean">
              <span>${escapeHtml(t("fields.includeModelo347"))}</span>
            </label>
            <label class="full-span">
              <span>${escapeHtml(t("fields.notes"))}</span>
              <textarea rows="3" data-decision-path="tax_treatment.notes">${escapeHtml(decision.tax_treatment.notes || "")}</textarea>
            </label>
          </div>
          <details class="review-technical-details">
            <summary>${escapeHtml(t("review.technicalDetails"))}</summary>
            <div class="form-grid compact-grid">
              <label>
                <span>${escapeHtml(t("fields.invoiceType"))}</span>
                <select data-decision-path="tax_treatment.aeat_invoice_type">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_invoice_type || [], decision.tax_treatment.aeat_invoice_type || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.operationKey"))}</span>
                <input type="text" data-decision-path="tax_treatment.aeat_operation_key" value="${escapeHtml(decision.tax_treatment.aeat_operation_key || "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.operationQualification"))}</span>
                <select data-decision-path="tax_treatment.aeat_operation_qualification">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_operation_qualification || [], decision.tax_treatment.aeat_operation_qualification || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.exemptionCode"))}</span>
                <select data-decision-path="tax_treatment.aeat_exemption_code" data-value-type="nullable-string">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_exemption_code || [], decision.tax_treatment.aeat_exemption_code || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.reverseCharge"))}</span>
                <select data-decision-path="tax_treatment.aeat_reverse_charge" data-value-type="nullable-boolean">
                  <option value=""${decision.tax_treatment.aeat_reverse_charge == null ? " selected" : ""}></option>
                  <option value="true"${decision.tax_treatment.aeat_reverse_charge === true ? " selected" : ""}>${escapeHtml(t("common.yes"))}</option>
                  <option value="false"${decision.tax_treatment.aeat_reverse_charge === false ? " selected" : ""}>${escapeHtml(t("common.no"))}</option>
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.expenseConcept"))}</span>
                <input type="text" data-decision-path="tax_treatment.aeat_expense_concept" data-value-type="nullable-string" value="${escapeHtml(decision.tax_treatment.aeat_expense_concept || "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.rateBasisPoints"))}</span>
                <input type="number" min="0" inputmode="numeric" data-decision-path="tax_treatment.rate_basis_points" data-value-type="integer" value="${escapeHtml(decision.tax_treatment.rate_basis_points ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.ruleVersion"))}</span>
                <input type="text" data-decision-path="tax_treatment.rule_version_id" data-value-type="nullable-string" value="${escapeHtml(decision.tax_treatment.rule_version_id || "")}">
              </label>
            </div>
          </details>
          ${issues.length ? `
            <div class="review-issues-grid">
              ${issues.map((issue, index) => renderReviewIssueResolution(issue, index, decision.issue_resolutions?.[index])).join("")}
            </div>` : ""}
        </section>

        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.result"))}</h2><small aria-live="polite">${escapeHtml(validationMessage)}</small></header>
          <div class="review-result-stack">
            <div class="review-alert ${state.review.validationResult ? "success" : "info"}">
              <strong>${escapeHtml(state.review.validationResult ? t("review.validationPassed") : t("review.validationPending"))}</strong>
              <p>${escapeHtml(state.review.validationResult ? t("review.validationPreview") : t("review.validationPending"))}</p>
            </div>
            ${state.review.validationResult ? `<pre class="review-preview">${escapeHtml(JSON.stringify(state.review.validationResult, null, 2))}</pre>` : ""}
            <div class="review-actions">
              <button type="button" class="secondary-button" id="review-refresh-button">${escapeHtml(t("common.refresh"))}</button>
              <button type="submit" class="primary-button" id="review-primary-button"${disabledWorkspace ? " disabled" : ""}>${escapeHtml(state.review.validationDirty ? t("review.primaryValidate") : (decision.outcome === "reject" ? t("review.primaryReject") : t("review.primaryApprove")))}</button>
            </div>
          </div>
        </section>
      </form>
    </div>
  `;

  document.querySelector("#review-back-button")?.addEventListener("click", () => {
    state.review.selectedReviewId = null;
    state.review.workItem = null;
    state.review.validationDirty = true;
    state.review.validationResult = null;
    state.review.error = "";
    renderReviewOverview();
  });

  document.querySelector("#review-refresh-button")?.addEventListener("click", async () => {
    try {
      await fetchReviewWorkItem(packet.review_id, {factsOnly: true});
      renderReviewWorkspace();
    } catch (error) {
      state.review.error = error.message;
      renderReviewWorkspace();
    }
  });

  document.querySelectorAll("[data-decision-path]").forEach((element) => {
    const eventName = element.tagName === "SELECT" || element.type === "checkbox" ? "change" : "input";
    element.addEventListener(eventName, () => {
      updateReviewDecision(element.dataset.decisionPath, readDecisionFieldValue(element));
      if (element.dataset.decisionPath === "outcome" && element.value === "reject") {
        updateReviewDecision("document_valid", true);
        const documentValid = document.querySelector('[data-decision-path="document_valid"]');
        if (documentValid) documentValid.value = "true";
      }
      markReviewValidationPending();
    });
  });

  document.querySelector("#review-fx-apply")?.addEventListener("click", async () => {
    const rateDate = document.querySelector("#review-fx-rate-date")?.value || "";
    const rate = document.querySelector("#review-fx-rate")?.value || "";
    const rateSource = document.querySelector("#review-fx-source")?.value || "";
    const sourceReference = document.querySelector("#review-fx-reference")?.value || "";
    try {
      const refreshed = await fetchJSON("/api/review/apply-fx", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          review_id: packet.review_id,
          rate_date: rateDate,
          rate,
          rate_source: rateSource,
          source_reference: sourceReference,
          expected_row_version: transaction.row_version,
        }),
      });
      const nextPacket = deepClone(refreshed.packet || refreshed);
      const draft = loadReviewDraft(currentReviewTransactionId());
      state.review.workItem = {
        ...state.review.workItem,
        ...refreshed,
        packet: mergeReviewDecisionFromDraft(nextPacket, draft, "facts"),
        requirements: (refreshed.requirements || state.review.workItem.requirements || []).map(normalizeReviewRequirement),
      };
      state.review.validationDirty = true;
      state.review.validationResult = null;
      state.review.error = "";
      persistReviewDraft(state.review.workItem.packet, {factsOnly: true});
      showToast(t("review.fxApplied"));
      renderReviewWorkspace();
    } catch (error) {
      state.review.error = error.message;
      renderReviewWorkspace();
    }
  });

  document.querySelector("#review-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const currentPacket = currentReviewPacket();
    if (!currentPacket) return;
    try {
      const endpoint = state.review.validationDirty ? "/api/review/validate" : "/api/review/apply";
      const result = await fetchJSON(endpoint, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({packet: currentPacket}),
      });
      if (state.review.validationDirty) {
        state.review.validationDirty = false;
        state.review.validationResult = result;
        state.review.error = "";
        persistReviewDraft(currentPacket);
        renderReviewWorkspace();
        return;
      }
      clearReviewDraft(currentReviewTransactionId());
      showToast(t("review.applySuccess"));
      state.review.selectedReviewId = null;
      state.review.workItem = null;
      state.review.validationDirty = true;
      state.review.validationResult = null;
      state.review.error = "";
      await renderReview();
    } catch (error) {
      state.review.error = error.message;
      renderReviewWorkspace();
    }
  });
}

async function renderAssets() {
  const rows = await fetchJSON("/api/assets");
  app.innerHTML = `
    <div class="table-toolbar"><h2>${escapeHtml(t("assets.title"))}</h2></div>
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead><tr><th>${escapeHtml(t("assets.asset"))}</th><th>${escapeHtml(t("assets.inService"))}</th><th>${escapeHtml(t("assets.cost"))}</th><th>${escapeHtml(t("assets.base"))}</th><th>${escapeHtml(t("assets.businessUse"))}</th><th>${escapeHtml(t("assets.rate"))}</th><th>${escapeHtml(t("assets.schedule"))}</th><th>${escapeHtml(t("assets.decision"))}</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td class="cell-primary"><strong>${escapeHtml(row.description || row.asset_code)}</strong><small>${escapeHtml(row.source_invoice_number || "")}</small></td>
                <td>${formatDate(row.placed_in_service_on)}</td>
                <td class="amount">${eur(row.cost)}</td>
                <td class="amount">${eur(row.amortizable_base)}</td>
                <td>${row.business_use_percent ? `${escapeHtml(row.business_use_percent)}%` : "—"}</td>
                <td>${row.annual_rate_percent ? `${escapeHtml(row.annual_rate_percent)}%` : "—"}</td>
                <td>${row.schedule_rows} · ${eur(row.scheduled)}</td>
                <td>${badge(row.advisor_decision || "unknown")}</td>
              </tr>`).join("") || emptyRow(8)}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

async function renderTaxes() {
  const data = await fetchJSON(`/api/taxes?period=${encodeURIComponent(state.period)}`);
  const obligations = obligationMap(data.obligations);
  const m130 = formCardData(data.tax_forms?.[FORM_KEYS[130]], obligations[130]);
  const m303 = formCardData(data.tax_forms?.[FORM_KEYS[303]], obligations[303]);
  app.innerHTML = `
    <div class="tax-layout">
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("taxes.obligations"))}</h2><small>${escapeHtml(data.period)}</small></header>
        <div class="table-wrap">
          <table>
            <thead><tr><th>${escapeHtml(t("taxes.form"))}</th><th>${escapeHtml(t("taxes.applicability"))}</th><th>${escapeHtml(t("taxes.status"))}</th><th>${escapeHtml(t("taxes.directDebit"))}</th><th>${escapeHtml(t("taxes.deadline"))}</th></tr></thead>
            <tbody>
              ${data.obligations.map((row) => `
                <tr>
                  <td><strong>Modelo ${escapeHtml(row.obligation_code)}</strong></td>
                  <td>${badge(row.determination)}</td>
                  <td>${badge(row.filing_status)}</td>
                  <td>${formatDate(row.direct_debit_cutoff_on)}</td>
                  <td>${formatDate(row.statutory_due_on)}</td>
                </tr>`).join("") || emptyRow(5)}
            </tbody>
          </table>
        </div>
      </section>
      <div class="section-stack">
        <section class="panel">
          <header class="panel-header"><h2>Modelo 130</h2><small>${escapeHtml(formSubtitle(m130, obligations[130]))}</small></header>
          ${casillas(m130.values || {}, ["01", "02", "03", "04", "05", "07", "19", "difficult_expenses"], formEmptyState(m130))}
        </section>
        <section class="panel">
          <header class="panel-header"><h2>Modelo 303</h2><small>${escapeHtml(formSubtitle(m303, obligations[303]))}</small></header>
          ${casillas(m303.values || {}, ["29", "45", "64", "69", "71", "72", "result", "compensation_carryforward"], formEmptyState(m303))}
        </section>
      </div>
    </div>
  `;
}

async function renderContacts() {
  const rows = await fetchJSON("/api/counterparties");
  app.innerHTML = `
    <div class="table-toolbar"><h2>${escapeHtml(t("contacts.title"))}</h2></div>
    <section class="panel">
      <div class="table-wrap">
        <table>
          <thead><tr><th>${escapeHtml(t("contacts.name"))}</th><th>${escapeHtml(t("contacts.country"))}</th><th>NIF / VAT ID</th><th>ROI</th><th>${escapeHtml(t("contacts.transactions"))}</th><th>${escapeHtml(t("contacts.last"))}</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td class="cell-primary"><strong>${escapeHtml(row.display_name)}</strong></td>
                <td>${escapeHtml(row.country_code || "—")}</td>
                <td>${escapeHtml(row.vat_id || row.tax_id || "—")}</td>
                <td>${badge(row.roi_status || "unknown")}</td>
                <td>${row.transaction_count}</td>
                <td>${formatDate(row.last_transaction_on)}</td>
              </tr>`).join("") || emptyRow(6)}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

async function refreshDashboard() {
  refreshButton.disabled = true;
  refreshButton.textContent = "…";
  try {
    await requestDashboardRefresh();
    state.posting.staleRefresh = null;
    await renderCurrentView();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "↻";
  }
}

function navigateToView(view) {
  if (!view) return;
  if (view === state.view) {
    applyViewState();
    return;
  }
  state.view = view;
  applyViewState();
  void renderCurrentView();
}

async function requestDashboardRefresh({showSuccessToast = true} = {}) {
  const response = await fetchJSON("/api/dashboard/refresh", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({period: state.period, as_of: new Date().toISOString().slice(0, 10)}),
  });
  if (showSuccessToast) showToast(t("refresh.done", {period: state.period}));
  return response;
}

async function retryPostingRefresh() {
  try {
    await requestDashboardRefresh();
    state.posting.staleRefresh = null;
    await renderCurrentView();
  } catch (error) {
    state.posting.staleRefresh = {period: state.period, error: error.message};
    showToast(error.message, true);
    await renderCurrentView();
  }
}

function currentPostingPreview() {
  return state.posting.previewPeriod === state.period ? state.posting.preview : null;
}

function integerValue(value, fallback = 0) {
  const number = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(number) ? number : fallback;
}

function dedupeMessages(messages) {
  const seen = new Set();
  return messages.filter((message) => {
    const text = String(message || "").trim();
    if (!text || seen.has(text)) return false;
    seen.add(text);
    return true;
  });
}

function reviewIdToTransactionId(reviewId) {
  const text = String(reviewId || "").trim();
  return text.startsWith("transaction:") ? text.slice("transaction:".length) : "";
}

function normalizePostingReasons(raw = {}) {
  return dedupeMessages([
    ...(Array.isArray(raw.reasons) ? raw.reasons : []),
    ...(Array.isArray(raw.blockers) ? raw.blockers : []),
    ...(Array.isArray(raw.blocking_issues) ? raw.blocking_issues : []),
  ].map((reason) => {
    if (reason && typeof reason === "object") {
      return String(reason.message || reason.reason || reason.code || "").trim();
    }
    return String(reason || "").trim();
  }));
}

function normalizePostingItem(raw = {}) {
  const detail = raw.result && typeof raw.result === "object" ? raw.result : {};
  const reviewId = String(raw.review_id || detail.review_id || "").trim();
  const transactionId = String(raw.transaction_id || detail.transaction_id || reviewIdToTransactionId(reviewId) || "").trim();
  const expectedRowVersion = Number.isInteger(raw.expected_row_version)
    ? raw.expected_row_version
    : Number.isInteger(detail.expected_row_version)
      ? detail.expected_row_version
      : Number.isInteger(raw.row_version)
        ? raw.row_version
        : Number.isInteger(detail.row_version)
          ? detail.row_version
      : null;
  const reasonMessages = dedupeMessages([
    ...normalizePostingReasons(detail),
    ...normalizePostingReasons(raw),
  ]);
  const cleanupStatus = String(
    raw.cleanup_status
      || detail.cleanup_status
      || raw.inbox_cleanup?.status
      || detail.inbox_cleanup?.status
      || ""
  ).trim();
  const effectiveAmountMinor = Number.isInteger(raw.effective_amount_eur_minor)
    ? raw.effective_amount_eur_minor
    : Number.isInteger(detail.effective_amount_eur_minor)
      ? detail.effective_amount_eur_minor
      : null;
  const reasonCode = String(raw.reason_code || detail.reason_code || "").trim();
  const message = dedupeMessages([
    String(raw.message || detail.message || raw.reason || detail.reason || "").trim(),
    reasonCode,
    reasonCode === "posted_cleanup_failed" ? statusLabel("posted_cleanup_failed") : "",
    ...reasonMessages,
  ]).join(" · ");
  return {
    reviewId,
    transactionId,
    expectedRowVersion,
    transactionDate: String(raw.transaction_date || detail.transaction_date || "").trim(),
    entryType: String(raw.entry_type || detail.entry_type || "").trim(),
    description: String(raw.description || detail.description || raw.counterparty_name || detail.counterparty_name || "").trim(),
    amountEur: raw.amount_eur == null && detail.amount_eur == null
      ? (effectiveAmountMinor == null ? "" : String(effectiveAmountMinor / 100))
      : String(raw.amount_eur ?? detail.amount_eur).trim(),
    rowStatus: String(raw.outcome || raw.status || raw.state || detail.outcome || detail.status || "").trim(),
    message,
    reasons: reasonMessages,
    cleanupApplied: Boolean(raw.cleanup_applies ?? detail.cleanup_applies ?? raw.cleanup?.applicable ?? detail.cleanup?.applicable),
    cleanupBlocked: Boolean(raw.cleanup_blocked ?? detail.cleanup_blocked ?? raw.cleanup?.blocking ?? detail.cleanup?.blocking),
    readyToPost: Boolean(raw.ready_to_post),
  };
}

function normalizePostingItems(rows) {
  return Array.isArray(rows) ? rows.map((row) => normalizePostingItem(row)) : [];
}

function normalizePostingSummary(summary, collections = {}) {
  const readyCount = integerValue(summary?.ready_count, integerValue(summary?.ready_to_post, collections.ready?.length || 0));
  const deferredCount = integerValue(summary?.deferred_count, collections.deferred?.length || 0);
  const blockedCount = integerValue(summary?.blocked_count, collections.blocked?.length || 0);
  const allRows = [
    ...(collections.ready || []),
    ...(collections.deferred || []),
    ...(collections.blocked || []),
  ];
  const readyMinorTotal = integerValue(
    summary?.ready_total_eur_minor,
    integerValue(summary?.ready_income_eur_minor, 0) + integerValue(summary?.ready_expense_eur_minor, 0)
  );
  return {
    readyCount,
    deferredCount,
    blockedCount,
    approvedCount: integerValue(summary?.approved_count, readyCount + deferredCount + blockedCount),
    readyTotalEur: String(
      summary?.ready_total_eur
      || summary?.ready_amount_eur
      || (readyMinorTotal / 100).toFixed(2)
    ),
    cleanupCount: integerValue(summary?.cleanup_count, allRows.filter((row) => row.cleanupApplied).length),
    cleanupBlockedCount: integerValue(summary?.cleanup_blocked_count, allRows.filter((row) => row.cleanupBlocked).length),
  };
}

function normalizePostingPreview(payload) {
  const legacyItems = normalizePostingItems(payload?.items);
  const ready = Array.isArray(payload?.ready)
    ? normalizePostingItems(payload.ready)
    : legacyItems.filter((row) => row.readyToPost);
  const deferred = Array.isArray(payload?.deferred)
    ? normalizePostingItems(payload.deferred)
    : [];
  const blocked = Array.isArray(payload?.blocked)
    ? normalizePostingItems(payload.blocked)
    : [];
  return {
    period: String(payload?.period || state.period || "").trim(),
    periodStatus: String(payload?.period_status || payload?.status || "open").trim() || "open",
    generatedAt: payload?.generated_at || payload?.as_of || null,
    asOf: payload?.as_of || null,
    summary: normalizePostingSummary(payload?.summary || payload?.posting_summary || {}, {ready, deferred, blocked}),
    ready,
    deferred,
    blocked,
  };
}

function postingItemMap(rows) {
  const map = new Map();
  rows.forEach((row) => {
    if (row.reviewId) map.set(row.reviewId, row);
    if (row.transactionId) map.set(row.transactionId, row);
  });
  return map;
}

function normalizePostingResult(raw, readyRows) {
  const knownRows = postingItemMap(readyRows);
  const sourceResults = Array.isArray(raw?.results) ? raw.results : [];
  const results = sourceResults.length
    ? sourceResults.map((row) => {
      const normalized = normalizePostingItem(row);
      const fallback = knownRows.get(normalized.reviewId) || knownRows.get(normalized.transactionId) || {};
      return {
        ...fallback,
        ...normalized,
        transactionDate: normalized.transactionDate || fallback.transactionDate || "",
        entryType: normalized.entryType || fallback.entryType || "",
        description: normalized.description || fallback.description || "",
        amountEur: normalized.amountEur || fallback.amountEur || "",
        rowStatus: normalized.rowStatus || String(raw?.status || "unknown"),
      };
    })
    : readyRows.map((row) => ({
      ...row,
      rowStatus: ["ok", "completed"].includes(String(raw?.status || "")) ? "posted" : String(raw?.status || "unknown"),
      message: String(raw?.message || "").trim(),
    }));
  return {
    status: String(raw?.status || "unknown"),
    interrupted: Boolean(raw?.interrupted) || String(raw?.status || "") === "interrupted",
    summary: {
      postedCount: integerValue(
        raw?.summary?.posted_count,
        integerValue(
          raw?.summary?.posted,
          integerValue(
            raw?.posted_count,
              integerValue(
                raw?.posted,
              integerValue(raw?.processed, results.filter((row) => ["posted", "included_in_snapshot", "ok", "completed"].includes(row.rowStatus)).length)
            )
          )
        )
      ),
    },
    results,
  };
}

function buildPostReadyItems(rows) {
  return rows.map((row) => {
    return {
      transaction_id: row.transactionId || reviewIdToTransactionId(row.reviewId || ""),
      expected_row_version: row.expectedRowVersion,
    };
  });
}

function postingActionDisabled(preview) {
  return state.posting.isSubmitting || preview.periodStatus !== "open" || preview.ready.length === 0;
}

function postingActionHint(preview) {
  if (preview.periodStatus !== "open") return t("review.postingClosedHint");
  if (!preview.ready.length) return t("review.postingNothingReady");
  return t("review.postingReadyHint");
}

function postingPreviewTimestamp(preview) {
  const value = preview.generatedAt || preview.asOf;
  return value ? t("review.postingGeneratedAt", {date: formatDateTime(value)}) : t("review.postingGeneratedUnknown");
}

function postingStat(label, value, detail = "") {
  return `
    <div class="posting-stat">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(String(value))}</strong>
      <small>${escapeHtml(detail || " ")}</small>
    </div>`;
}

function renderPostingRefreshWarning() {
  const stale = state.posting.staleRefresh;
  if (!stale || stale.period !== state.period) return "";
  return `
    <div class="period-note posting-warning">
      <div>
        <strong>${escapeHtml(t("review.postingStaleWarning"))}</strong>
        ${stale.error ? `<p>${escapeHtml(stale.error)}</p>` : ""}
      </div>
      <button type="button" class="secondary-button" data-posting-action="retry-refresh">${escapeHtml(t("review.postingRetryRefresh"))}</button>
    </div>`;
}

function renderPostingPeriodWarning(preview) {
  if (preview.periodStatus === "open") return "";
  return `
    <div class="period-note posting-warning">
      <div>
        <strong>${escapeHtml(t("review.postingClosedPeriod"))}</strong>
        <p>${escapeHtml(t("review.postingClosedHint"))}</p>
      </div>
    </div>`;
}

function renderPostingQueueSection(title, rows) {
  if (!rows.length) return "";
  return `
    <div>
      <h3 class="posting-section-label">${escapeHtml(title)}</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("transactions.status"))}</th><th>${escapeHtml(t("transactions.amount"))}</th><th>${escapeHtml(t("review.postingResultMessage"))}</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${formatDate(row.transactionDate)}</td>
                <td class="cell-primary"><strong>${escapeHtml(row.description || row.reviewId || row.transactionId || t("common.noId"))}</strong><small>${escapeHtml(row.entryType || "—")}</small></td>
                <td>${badge(row.rowStatus || "unknown")}</td>
                <td class="amount">${row.amountEur ? eur(row.amountEur) : "—"}</td>
                <td class="posting-result-message">${escapeHtml(row.message || row.reasons?.join(" · ") || "—")}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
}

function renderPostingResultSection() {
  if (state.posting.lastResultPeriod !== state.period || !state.posting.lastResult) return "";
  return `
    <div class="posting-results">
      <div class="posting-results-header">
        <h3>${escapeHtml(t("review.postingResults"))}</h3>
        <p>${escapeHtml(t("review.postingResultSummary", {
          status: statusLabel(state.posting.lastResult.status),
          count: state.posting.lastResult.summary.postedCount,
        }))}</p>
      </div>
      ${renderPostingQueueSection(t("review.postingRows"), state.posting.lastResult.results)}
    </div>`;
}

function openPostingConfirmDialog() {
  const preview = currentPostingPreview();
  if (!preview || postingActionDisabled(preview)) return;
  state.posting.pendingItems = preview.ready.map((row) => ({...row}));
  postingConfirmPeriod.textContent = preview.period || state.period;
  postingConfirmStatus.textContent = "";
  renderPostingConfirmDialog();
  if (!postingConfirmDialog.open) postingConfirmDialog.showModal();
}

function renderPostingConfirmDialog() {
  const preview = currentPostingPreview();
  const rows = state.posting.pendingItems;
  postingConfirmBody.innerHTML = `
    <p>${escapeHtml(t("review.postConfirmLead"))}</p>
    ${preview && (preview.summary.cleanupCount > 0 || preview.summary.cleanupBlockedCount > 0) ? `
      <div class="period-note posting-warning">
        <div>
          <strong>${escapeHtml(t("review.postConfirmCleanupWarning", {
            cleanupCount: preview.summary.cleanupCount,
            cleanupBlockedCount: preview.summary.cleanupBlockedCount,
          }))}</strong>
        </div>
      </div>` : ""}
    ${renderPostingQueueSection(t("review.postingRows"), rows)}
  `;
  confirmPostingButton.disabled = state.posting.isSubmitting || rows.length === 0;
  confirmPostingButton.textContent = t("review.postConfirmAction");
}

function closePostingConfirmDialog() {
  if (postingConfirmDialog.open) postingConfirmDialog.close();
  postingConfirmStatus.textContent = "";
  state.posting.pendingItems = [];
}

function postingResultToast(result) {
  if (result.status === "interrupted") return t("review.postInterrupted");
  if (result.status === "partial") return t("review.postPartial");
  if (result.summary.postedCount > 0) return t("review.postSuccess", {count: result.summary.postedCount});
  return t("review.postNone");
}

async function submitPostingReady() {
  const preview = currentPostingPreview();
  if (!preview || postingActionDisabled(preview)) return;
  const pendingItems = state.posting.pendingItems.length ? state.posting.pendingItems : preview.ready;
  state.posting.isSubmitting = true;
  renderPostingConfirmDialog();
  try {
    const payload = await fetchJSON("/api/review/post-ready", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        period: state.period,
        items: buildPostReadyItems(pendingItems),
      }),
    });
    state.posting.lastResult = normalizePostingResult(payload, pendingItems);
    state.posting.lastResultPeriod = state.period;
    closePostingConfirmDialog();
    if (state.posting.lastResult.summary.postedCount > 0) {
      try {
        await requestDashboardRefresh({showSuccessToast: false});
        state.posting.staleRefresh = null;
      } catch (error) {
        state.posting.staleRefresh = {period: state.period, error: error.message};
      }
    }
    state.posting.isSubmitting = false;
    await renderCurrentView();
    showToast(postingResultToast(state.posting.lastResult));
  } catch (error) {
    postingConfirmStatus.textContent = error.message;
    showToast(error.message, true);
  } finally {
    state.posting.isSubmitting = false;
    if (postingConfirmDialog.open) renderPostingConfirmDialog();
  }
}

function localDateKey(today = new Date()) {
  const year = String(today.getFullYear());
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentQuarterKey(today = new Date()) {
  const year = today.getFullYear();
  const month = today.getMonth() + 1;
  return `${year}-Q${Math.floor((month - 1) / 3) + 1}`;
}

function quarterKeyForDateKey(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return "";
  const month = Number(match[2]);
  if (!Number.isInteger(month) || month < 1 || month > 12) return "";
  return `${match[1]}-Q${Math.floor((month - 1) / 3) + 1}`;
}

function resolveCopyTargetPeriod(periods, today = new Date()) {
  const currentPeriodKey = currentQuarterKey(today);
  const currentOpen = periods.find(
    (period) => period.period_key === currentPeriodKey && period.status === "open"
  );
  if (currentOpen) return currentOpen.period_key;
  const fallback = periods.find((period) => period.status === "open");
  return fallback ? fallback.period_key : null;
}

function refreshCopyTargetState() {
  if (!state.bootstrap?.intake_enabled) {
    state.copyTargetPeriodKey = null;
    state.copyTargetIsCurrentQuarter = false;
    return;
  }
  const today = new Date();
  state.copyTargetPeriodKey = resolveCopyTargetPeriod(state.bootstrap.periods || [], today);
  state.copyTargetIsCurrentQuarter = state.copyTargetPeriodKey === currentQuarterKey(today);
}

function resolveCopyIssuedOn(sourceDate, {sourcePeriodKey, targetPeriodKey, targetIsCurrentQuarter, today = new Date()}) {
  if (sourcePeriodKey === targetPeriodKey) return String(sourceDate || "");
  if (targetIsCurrentQuarter) return localDateKey(today);
  return "";
}

function setIntakeNotice(lines) {
  intakeNotice.replaceChildren();
  lines.forEach((line) => {
    const text = String(line || "").trim();
    if (!text) return;
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    intakeNotice.append(paragraph);
  });
  intakeNotice.hidden = intakeNotice.children.length === 0;
}

function replaceIncomeCopyRows(rows) {
  incomeCopyRowsById.clear();
  rows.forEach((row) => {
    if (row?.entry_type !== "income" || !row?.transaction_id) return;
    incomeCopyRowsById.set(String(row.transaction_id), row);
  });
}

const COPY_BLOCKED_STATUSES = new Set(["duplicate", "rejected", "void"]);

function transactionRenderToken(entryType, renderGeneration = currentRenderGeneration) {
  return `${renderGeneration}:${state.view}:${state.period}:${entryType}`;
}

function isActiveTransactionRenderToken(token, entryType, renderGeneration = currentRenderGeneration) {
  return renderGeneration === currentRenderGeneration
    && token === transactionRenderToken(entryType, renderGeneration);
}

function buildIncomeCopyPrefill(row, context) {
  const prefill = {
    issued_on: "",
    counterparty_name: String(row.counterparty_name || "").trim(),
    document_number: String(row.document_number || "").trim(),
    currency: "",
    gross: "",
  };
  const noticeLines = [t("intake.copyNotice", {period: context.targetPeriodKey})];
  const candidateIssuedOn = resolveCopyIssuedOn(row.transaction_date, context);
  prefill.issued_on = quarterKeyForDateKey(candidateIssuedOn) === context.targetPeriodKey
    ? candidateIssuedOn
    : "";

  const currency = String(row.currency || "").trim().toUpperCase();
  const amountOriginal = row.amount_original == null ? "" : String(row.amount_original).trim();
  const hasSupportedAmount = amountOriginal !== "" && Number.isFinite(Number(amountOriginal)) && Number(amountOriginal) >= 0;
  const currencyOptions = new Set(
    Array.from(intakeForm.elements.currency.options, (option) => option.value)
  );
  if (currency && hasSupportedAmount && currencyOptions.has(currency)) {
    prefill.currency = currency;
    prefill.gross = amountOriginal;
  }
  if (currency && !currencyOptions.has(currency)) {
    noticeLines.push(t("intake.copyUnsupportedCurrency", {currency}));
  }
  if (amountOriginal !== "" && !hasSupportedAmount) {
    noticeLines.push(t("intake.copyInvalidAmount"));
  }

  return {prefill, noticeLines};
}

function isCopyableIncomeRow(row, {copyable = false, targetPeriodKey = state.copyTargetPeriodKey} = {}) {
  return Boolean(copyable)
    && Boolean(targetPeriodKey)
    && row?.entry_type === "income"
    && Boolean(row?.transaction_id)
    && !COPY_BLOCKED_STATUSES.has(row.lifecycle_status)
    && !COPY_BLOCKED_STATUSES.has(row.document_status);
}

function copyTransactionAction(transactionId, targetPeriodKey = state.copyTargetPeriodKey) {
  const label = escapeHtml(t("common.copyToPeriod", {period: targetPeriodKey}));
  return `<button type="button" class="copy-action-button" data-copy-transaction-id="${escapeHtml(transactionId)}" title="${label}" aria-label="${label}">
    <svg class="copy-action-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M7.75 2.75h6a2.5 2.5 0 0 1 2.5 2.5v6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path>
      <rect x="5.25" y="5.25" width="9.5" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.5"></rect>
      <path d="M5.25 8.25h-1a2.5 2.5 0 0 1-2.5-2.5v-1a2.5 2.5 0 0 1 2.5-2.5h6.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></path>
    </svg>
  </button>`;
}

function openIntake(kind, {targetPeriodKey = state.period, prefill = null, noticeLines = []} = {}) {
  intakeForm.reset();
  setIntakeKind(kind);
  intakePeriod.value = targetPeriodKey;
  document.querySelector("#intake-period-label").textContent = targetPeriodKey;
  intakeStatus.textContent = "";
  const formElements = intakeForm.elements;
  formElements.issued_on.value = prefill?.issued_on || "";
  formElements.counterparty_name.value = prefill?.counterparty_name || "";
  formElements.document_number.value = prefill?.document_number || "";
  formElements.currency.value = prefill?.currency || "EUR";
  formElements.gross.value = prefill?.gross || "";
  setIntakeNotice(noticeLines);
  updateFilePrompt();
  if (!dialog.open) dialog.showModal();
}

function closeIntake() {
  if (dialog.open) dialog.close();
}

function setIntakeKind(kind) {
  state.intakeKind = kind;
  intakeKind.value = kind;
  document.querySelectorAll(".segmented-control button").forEach((button) => {
    button.classList.toggle("active", button.dataset.kind === kind);
  });
  document.querySelector("#counterparty-label").textContent =
    kind === "income_invoice" ? t("fields.client") : t("fields.supplier");
  if (!intakeFile.files[0]) updateFilePrompt();
  document.querySelectorAll(".expense-only").forEach((element) => {
    element.hidden = kind === "income_invoice";
  });
}

function updateFilePrompt() {
  fileLabel.textContent = state.intakeKind === "income_invoice"
    ? t("intake.incomeFile")
    : t("intake.expenseFile");
}

function countNoun(value, noun) {
  const count = Number(value) || 0;
  const category = new Intl.PluralRules(intlLocale()).select(count);
  const forms = nounMessages[state.locale][noun];
  const label = forms[category] || forms.other;
  return `${count} ${label}`;
}

function waitingForPeriod(value) {
  const count = Number(value) || 0;
  const category = new Intl.PluralRules(intlLocale()).select(count);
  const key = category === "one"
    ? "dashboard.waitingForPeriodOne"
    : "dashboard.waitingForPeriodOther";
  return t(key, {count});
}

function obligationMap(obligations) {
  const map = {};
  (obligations || []).forEach((row) => {
    map[Number(row?.obligation_code)] = row;
  });
  return map;
}

function formCardData(form, obligation, {warnOnMissingHeadline = false} = {}) {
  const fallback = form || {
    form_code: "",
    display_state: "unavailable",
    filed_on: null,
    values: {},
    headline_value: null,
    headline_detail: null,
    preview_as_of: null,
    extraction_status: null,
  };
  const zeroSafeHeadline = fallback.headline_value !== null && fallback.headline_value !== undefined;
  const needsUnavailableWarning = warnOnMissingHeadline
    && !zeroSafeHeadline
    && fallback.display_state === "filed";
  return {
    ...fallback,
    display_state: needsUnavailableWarning ? "filed_without_values" : fallback.display_state,
    obligationFiled: obligation?.filing_status === "filed",
  };
}

function formCardValue(form) {
  return form.headline_value !== null && form.headline_value !== undefined
    ? eur(form.headline_value)
    : "—";
}

function formAccentClass(form) {
  return ["filed", "snapshot_only"].includes(form.display_state) ? "" : "warning";
}

function formSubtitle(form, obligation) {
  const date = form.filed_on ? formatDate(form.filed_on) : null;
  const previewDate = form.preview_as_of ? formatDate(form.preview_as_of) : null;
  let stateText = "";
  if (form.display_state === "filed") {
    stateText = date ? t("dashboard.filedOn", {date}) : t("dashboard.filed");
  } else if (form.display_state === "filed_without_values") {
    const filedLabel = date ? t("dashboard.filedOn", {date}) : t("dashboard.filed");
    stateText = `${filedLabel} · ${t("dashboard.valuesUnavailable")}`;
  } else if (form.display_state === "snapshot_only") {
    stateText = t("dashboard.snapshotAvailable");
  } else if (form.display_state === "preview") {
    stateText = previewDate ? t("dashboard.calculatedAsOf", {date: previewDate}) : t("dashboard.notCalculated");
  } else {
    stateText = t("dashboard.notCalculated");
  }
  if (obligation?.filing_status === "filed" && ["preview", "unavailable"].includes(form.display_state)) {
    stateText = `${t("dashboard.filed")} · ${stateText}`;
  }
  if (form.headline_detail !== null && form.headline_detail !== undefined && !["filed_without_values", "unavailable"].includes(form.display_state)) {
    return `${stateText} · ${t("dashboard.carryForward", {amount: eur(form.headline_detail)})}`;
  }
  return stateText;
}

function showApprovedActivityBanner(summary) {
  if (!summary.approvedCount) return "";
  return `
    <div class="period-note posting-banner">
      <div>
        <strong>${escapeHtml(t("dashboard.postingBannerTitle", {count: summary.approvedCount}))}</strong>
        <p>${escapeHtml(t("dashboard.approvedNotPosted"))}</p>
      </div>
      <button type="button" class="secondary-button" data-nav-view="review">${escapeHtml(t("dashboard.postingBannerCta"))}</button>
    </div>`;
}

function formEmptyState(form) {
  if (form.display_state === "filed_without_values") {
    if (form.extraction_status === "values_unavailable") return t("taxes.filedValuesUnavailableExtract");
    if (form.extraction_status === "pdf_unreadable") return t("taxes.filedValuesUnavailablePdf");
    return t("taxes.filedValuesUnavailable");
  }
  return t("taxes.calculationMissing");
}

function intlLocale() {
  return state.locale === "en" ? "en-GB" : "ru-RU";
}

function t(key, variables = {}) {
  const template = messages[state.locale][key] || messages.ru[key] || key;
  return Object.entries(variables).reduce(
    (result, [name, value]) => result.replaceAll(`{${name}}`, String(value)),
    template
  );
}

function loadLocale() {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (SUPPORTED_LOCALES.has(stored)) return stored;
  } catch {
    // The UI remains usable when browser storage is disabled.
  }
  return "ru";
}

function storeLocale(locale) {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Locale still applies for the current page when storage is unavailable.
  }
}

async function changeLocale(locale) {
  if (!SUPPORTED_LOCALES.has(locale)) return;
  const changed = state.locale !== locale;
  state.locale = locale;
  storeLocale(locale);
  applyStaticTranslations();
  if (changed && state.period) await renderCurrentView();
}

function applyStaticTranslations() {
  if (!hasDOM) return;
  document.documentElement.lang = state.locale;
  document.title = t("app.title");
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAria));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((element) => {
    element.setAttribute("title", t(element.dataset.i18nTitle));
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder));
  });
  localeButtons.forEach((button) => {
    const active = button.dataset.locale === state.locale;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  applyViewState();
  setIntakeKind(state.intakeKind);
  if (postingConfirmDialog?.open) renderPostingConfirmDialog();
}

function applyViewState() {
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
  pageTitle.textContent = t(`titles.${state.view}`);
}

if (typeof globalThis !== "undefined") {
  globalThis.__AUTONOMO_WEB_UI_TEST_HOOKS__ = {
    buildReviewDraftStorageKey,
    buildRequirementSteps,
    countNoun: (value, noun, locale) => {
      const previousLocale = state.locale;
      state.locale = locale || previousLocale;
      const result = countNoun(value, noun);
      state.locale = previousLocale;
      return result;
    },
    evaluateWorkItemPosting,
    postingStatusLabelForWorkItem,
    statusLabel: (value, locale) => {
      const previousLocale = state.locale;
      state.locale = locale || previousLocale;
      const result = statusLabel(value);
      state.locale = previousLocale;
      return result;
    },
    summarizeReviewRows,
    integerValue,
    dedupeMessages,
    reviewIdToTransactionId,
    normalizePostingReasons,
    normalizePostingItem,
    normalizePostingItems,
    normalizePostingSummary,
    normalizePostingPreview,
    postingItemMap,
    normalizePostingResult,
    buildPostReadyItems,
  };
}

if (hasDOM) {
  localeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      void changeLocale(button.dataset.locale);
    });
  });

  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      if (state.view !== "review") {
        state.review.selectedReviewId = null;
        state.review.workItem = null;
        state.review.validationDirty = true;
        state.review.validationResult = null;
        state.review.error = "";
      }
      document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      pageTitle.textContent = t(`titles.${state.view}`);
      renderCurrentView();
    });
  });

  periodSelect.addEventListener("change", () => {
    state.period = periodSelect.value;
    renderCurrentView();
  });

  newEntryButton.addEventListener("click", () => {
    const kind = state.view === "income" ? "income_invoice" : "expense_invoice";
    openIntake(kind);
  });

  refreshButton.addEventListener("click", refreshDashboard);
  document.querySelector("#close-dialog").addEventListener("click", closeIntake);
  document.querySelector("#cancel-dialog").addEventListener("click", closeIntake);
  document.querySelector("#close-posting-dialog").addEventListener("click", closePostingConfirmDialog);
  document.querySelector("#cancel-posting-dialog").addEventListener("click", closePostingConfirmDialog);
  confirmPostingButton.addEventListener("click", () => {
    void submitPostingReady();
  });

  document.querySelectorAll(".segmented-control button").forEach((button) => {
    button.addEventListener("click", () => setIntakeKind(button.dataset.kind));
  });

  intakeFile.addEventListener("change", () => {
    if (intakeFile.files[0]) fileLabel.textContent = intakeFile.files[0].name;
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    fileDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDrop.classList.add("dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    fileDrop.addEventListener(eventName, (event) => {
      event.preventDefault();
      fileDrop.classList.remove("dragging");
    });
  });

  fileDrop.addEventListener("drop", (event) => {
    if (!event.dataTransfer.files.length) return;
    const transfer = new DataTransfer();
    transfer.items.add(event.dataTransfer.files[0]);
    intakeFile.files = transfer.files;
    fileLabel.textContent = event.dataTransfer.files[0].name;
  });

  app.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const viewButton = event.target.closest("[data-nav-view]");
    if (viewButton) {
      navigateToView(viewButton.dataset.navView);
      return;
    }
    const postingButton = event.target.closest("[data-posting-action]");
    if (postingButton) {
      if (postingButton.dataset.postingAction === "open-confirm") {
        openPostingConfirmDialog();
        return;
      }
      if (postingButton.dataset.postingAction === "retry-refresh") {
        void retryPostingRefresh();
        return;
      }
    }
    const button = event.target.closest("[data-copy-transaction-id]");
    if (!button) return;
    const row = incomeCopyRowsById.get(button.dataset.copyTransactionId || "");
    if (!isCopyableIncomeRow(row, {copyable: true})) {
      showToast(t("intake.copyStale"), true);
      return;
    }
    const {prefill, noticeLines} = buildIncomeCopyPrefill(row, {
      sourcePeriodKey: state.period,
      targetPeriodKey: state.copyTargetPeriodKey,
      targetIsCurrentQuarter: state.copyTargetIsCurrentQuarter,
    });
    openIntake("income_invoice", {
      targetPeriodKey: state.copyTargetPeriodKey,
      prefill,
      noticeLines,
    });
    const documentNumber = intakeForm.elements.document_number;
    documentNumber.focus();
    documentNumber.select();
  });

  intakeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!intakeFile.files[0]) {
      intakeStatus.textContent = t("intake.selectFile");
      return;
    }
    submitIntake.disabled = true;
    intakeStatus.textContent = t("intake.processing");
    try {
      const result = await fetchJSON("/api/intake", {
        method: "POST",
        body: new FormData(intakeForm),
        fallbackMessage: t("intake.failed"),
      });
      intakeStatus.textContent = t("intake.accepted", {
        id: shortId(result.system_marker),
      });
      showToast(t("intake.acceptedToast", {period: result.period}));
      setTimeout(() => {
        closeIntake();
        renderCurrentView();
      }, 700);
    } catch (error) {
      intakeStatus.textContent = error.message;
      showToast(t("intake.failed"), true);
    } finally {
      submitIntake.disabled = false;
    }
  });

  applyStaticTranslations();
  init();
}
