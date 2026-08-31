const LOCALE_STORAGE_KEY = "autonomo.locale";
const REVIEW_DRAFT_STORAGE_PREFIX = "autonomo.review-draft";
const INTAKE_DRAFT_STORAGE_KEY = "autonomo.intake-draft";
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
    "expense.purchase": "Покупки, услуги и прочие расходы",
    "expense.amortization": "Амортизация техники",
    "expense.explanation": "Часть стоимости ранее купленной техники, учитываемая в расходах этого квартала. Это не новая покупка и не платёж.",
    "expense.recognitionDate": "Дата учёта",
    "expense.sourceDate": "Документ от {date}",
    "expense.sourceAmount": "Сумма документа: {amount}",
    "expense.recordedAmount": "Сумма исходной записи: {amount}",
    "expense.amount": "Сумма расхода",
    "expense.quarterAmount": "Амортизация за квартал",
    "expense.periodDate": "Период и дата учёта",
    "expense.assetDocument": "Актив / исходный документ",
    "expense.quarter": "{quarter} квартал {year}",
    "expense.forQuarter": "Амортизация за {period}",
    "expense.future": "Дата учёта ещё не наступила: {date}",
    "expense.unposted": "Не проведено",
    "expense.unmatched": "Актив не сопоставлен",
    "expense.inferred": "Сопоставлено по контрагенту и дате документа",
    "expense.source": "Источник",
    "expense.quarterTotal": "Проведено и проверено за весь квартал: {amount}",
    "expense.shown": "Показано {shown} из {count}",
    "expense.empty": "В этом квартале записей нет.",
    "expense.noMatches": "По запросу ничего не найдено.",
    "expense.moreRecords": "Эти записи находятся на следующих страницах. Нажмите «Показать ещё».",
    "expense.loadMore": "Показать ещё",
    "expense.retry": "Повторить загрузку",
    "expense.refreshData": "Обновить данные",
    "expense.refreshFailed": "Не удалось обновить данные. Показаны предыдущие значения.",
    "expense.invalidPage": "Не удалось загрузить следующую страницу. Повторите попытку.",
    "expense.loading": "Загружаем расходы…",
    "expense.search": "Контрагент, документ или актив",
    "expense.posted": "Проведено",
    "expense.approved": "Проверено, не проведено: {amount}",
    "expense.futureAmount": "Из них будущей датой: {amount}",
    "expense.missing": "Не хватает данных о сумме",
    "expense.inAmount": "В сумме амортизации",
    "expense.chartSourceNote": "График использует исходные суммы записей. Для амортизации это полная стоимость прежней покупки, а не новое списание: сумму за квартал смотрите в блоке выше.",
    "common.loadFailed": "Не удалось загрузить данные. Проверьте подключение и повторите попытку.",
    "common.retry": "Повторить",
    "common.sessionExpired": "Сессия истекла. Перезагрузите страницу, чтобы восстановить доступ.",
    "common.reload": "Перезагрузить страницу",
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
    "nav.settings": "Настройки",
    "storage.settings": "SQLite · Настройки",
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
    "intake.sourceAria": "Источник документа",
    "intake.sourceUpload": "Загрузить файл",
    "intake.sourceGoogleDrive": "Google Drive URL",
    "intake.googleDriveUrl": "Ссылка на файл Google Drive",
    "intake.googleDrivePlaceholder": "drive.google.com/file/d/...",
    "intake.googleDriveHint": "Файл останется в вашем архиве; будет сохранена ссылка на оригинал.",
    "intake.googlePickerHint": "Google Picker будет доступен после настройки узкого доступа drive.file.",
    "intake.googleDriveInvalid": "Введите ссылку на файл Google Drive.",
    "intake.chooseGoogleFolder": "Выбрать папку Google Drive",
    "intake.googleFolderSelected": "Папка: {name}",
    "intake.googlePickerUnavailable": "Выбор папки Google Drive пока не настроен.",
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
    "expense.open": "Открыть расход",
    "expense.title": "Просмотр расхода",
    "expense.notes": "Примечания",
    "expense.noNotes": "Примечаний нет",
    "expense.readOnly": "Только просмотр сохранённых данных",
    "expense.back": "К списку: {title} · {period}",
    "expense.notFound": "Расход не найден",
    "expense.wrongType": "Это не расход",
    "expense.refresh": "Обновить данные",
    "expense.description": "Описание",
    "titles.review": "Проверка",
    "titles.assets": "Активы",
    "titles.taxes": "Налоги и сроки",
    "titles.contacts": "Контрагенты",
    "titles.settings": "Настройки аккаунта",
    "contacts.cardTitle": "Карточка контрагента",
    "contacts.actionsFor": "Действия для {name}",
    "contacts.actions": "Дополнительные действия",
    "contacts.back": "К списку контрагентов",
    "contacts.backToParty": "К контрагенту",
    "contacts.facts": "Реквизиты",
    "contacts.operations": "Операции",
    "contacts.operationType": "Тип",
    "contacts.description": "Описание",
    "contacts.sourceDocument": "Документ-источник",
    "contacts.email": "Эл. почта",
    "contacts.phone": "Телефон",
    "contacts.allPeriods": "Все периоды",
    "contacts.more": "Показать ещё",
    "contacts.shown": "Показано {count} из {total}",
    "contacts.noOperations": "Операций за этот период нет.",
    "contacts.operationsError": "Не удалось загрузить операции.",
    "contacts.retry": "Повторить",
    "contacts.notFound": "Контрагент не найден.",
    "contacts.refresh": "Обновить карточку",
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
    "transactions.emptyIncome": "Доходов в этом периоде пока нет. Нажмите «Добавить», чтобы принять счёт.",
    "transactions.noMatches": "По запросу ничего не найдено.",
    "review.queueEmpty": "Очередь проверки пуста — всё проверено.",
    "review.transactions": "Операции на проверке",
    "review.documents": "Документы на проверке",
    "review.openIssues": "Открытые вопросы",
    "review.summary": "Очередь проведения",
    "review.summaryReady": "Можно провести сейчас",
    "review.summaryNeedsReview": "Нужно проверить",
    "review.category.ready": "можно провести",
    "review.category.later": "провести позже",
    "review.category.blocked": "проведение заблокировано",
    "review.category.needs_review": "нужна проверка",
    "reviewTabs.aria": "Разделы проверки",
    "reviewTabs.queue": "Очередь",
    "reviewTabs.posting": "Проведение",
    "reviewTabs.documents": "Документы и вопросы",
    "review.submitting": "Отправка…",
    "review.formDisabledHint": "Поля решений заблокированы, пока операцию нельзя провести — причина объяснена выше.",
    "intake.draftRestored": "Черновик восстановлен — проверьте поля перед приёмом.",
    "intake.consistencyHint": "База + IVA = {expected}, а итого — {total}. Проверьте суммы.",
    "intake.disabledReason": "Приём документов отключён: на сервере не настроен каталог входящих.",
    "toolbar.periodLocked": "Период зафиксирован, пока открыта карточка записи.",
    "tables.actions": "Действия",
    "review.summaryLater": "Можно будет провести позже",
    "review.summaryBlocked": "Блокировки после проверки",
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
    "review.invalidWorkItem": "Не удалось проверить операцию и её период. Обновите страницу или вернитесь к списку через меню «Проверка».",
    "review.outcomeApprove": "Подтвердить",
    "review.outcomeReject": "Отклонить",
    "review.actionResolve": "Закрыть вопрос",
    "review.actionKeepOpen": "Оставить открытым",
    "review.documentLink": "Открыть файл",
    "review.invoiceLabel": "Счет {number}",
    "review.issueOpen": "Открытый вопрос",
    "review.issueResolved": "Вопрос будет закрыт",
    "review.vatInvestment": "Классификация покупки для IVA",
    "review.vatCurrent": "Текущая покупка для IVA",
    "review.vatAsset": "Инвестиционный товар для IVA",
    "review.vatUnknown": "Классификация IVA не проверена",
    "review.vatInvestmentHint": "Амортизация для IRPF не определяет классификацию IVA. Выберите отдельно на основании покупки.",
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
    "assets.decision": "Состояние учёта",
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
    "contacts.editName": "Исправить имя",
    "contacts.nameLabel": "Имя",
    "contacts.renameHint": "Имя изменится в приложении и новых выгрузках. Исходные документы останутся без изменений.",
    "contacts.manualName": "Исправлено вручную",
    "contacts.saveName": "Сохранить",
    "contacts.savingName": "Сохранение…",
    "contacts.nameSaved": "Имя исправлено",
    "contacts.nameUnchanged": "Имя не изменилось",
    "contacts.invalidName": "Введите непустое имя в одну строку без управляющих символов.",
    "contacts.nameHistory": "История изменений",
    "contacts.noNameHistory": "Ручных исправлений пока нет.",
    "contacts.historyError": "Не удалось загрузить историю.",
    "contacts.retryHistory": "Повторить загрузку",
    "contacts.localActor": "Локальная сессия",
    "contacts.sheetActor": "Правка из таблицы",
    "contacts.nameConflict": "Контрагент изменился после открытия формы. Проверьте актуальное имя перед сохранением.",
    "contacts.currentName": "Актуальное имя: {name}",
    "contacts.acceptCurrentName": "Проверил: использовать текущую версию",
    "contacts.discardName": "Закрыть форму без сохранения исправленного имени?",
    "contacts.nameBusy": "База временно занята. Повторите сохранение.",
    "contacts.nameMissing": "Контрагент больше недоступен. Закройте форму и обновите список.",
    "contacts.nameRefreshError": "Имя сохранено, но список не обновился. Откройте раздел заново.",
    "refresh.done": "Расчет {period} обновлен",
    "documents.counterparty": "Контрагент",
    "documents.type": "Тип",
    "issues.none": "Открытых вопросов нет",
    "issues.sourceDetails": "Исходные сведения",
    "issues.default": "Требуется ручная проверка.",
    "routes.unknownTitle": "Страница не найдена",
    "routes.unknownHint": "Такого раздела нет. Откройте обзор, чтобы продолжить.",
    "routes.backToDashboard": "К обзору",
    "review.factsTitle": "Факты счёта",
    "review.questionsTitle": "Что нужно от вас",
    "review.confirmAction": "Подтвердить проверку",
    "review.confirmHint": "Проверка применяется одной операцией: курс (если нужен) и решение по счёту.",
    "review.confirmSuccess": "Проверка подтверждена, решение применено.",
    "review.rejectAction": "Отклонить счёт",
    "review.rejectTitle": "Отклонение счёта",
    "review.rejectLead": "Отклонение — отдельное действие: укажите валидность документа и причину.",
    "review.rejectConfirm": "Подтвердить отклонение",
    "review.rejectSuccess": "Счёт отклонён.",
    "review.rejectDocumentRequired": "Для отклонения нужно явно указать, валиден ли документ.",
    "review.rejectReasonRequired": "Для отклонения нужна причина.",
    "review.fxSuggestionLabel": "Справочный курс ECB / Banco de España",
    "review.fxExactRate": "Курс на {date}",
    "review.fxPriorRate": "Курс предыдущего рабочего дня, {date}",
    "review.fxUseManual": "Документированный settlement-курс",
    "review.fxExisting": "Курс уже применён к счёту",
    "review.fxUnavailable": "Справочный курс недоступен",
    "review.fxUnavailableHint": "Справочного курса на эту дату нет. Укажите документированный settlement-курс со ссылкой на документ.",
    "review.fxSettlementRate": "Settlement-курс (EUR за {currency})",
    "review.fxSettlementReference": "Ссылка на settlement-документ",
    "review.fxSettlementDate": "Дата settlement",
    "review.fxAmount": "Сумма в EUR",
    "review.fxNeeded": "Курс не выбран",
    "review.fxChoiceNote": "Справочный курс — не фактический банковский курс.",
    "review.autoFilledTitle": "Заполнено автоматически",
    "review.autoFilledIntake": "из приёма документа",
    "review.autoFilledExtraction": "из извлечённых значений",
    "review.autoFilledDefault": "значение по умолчанию",
    "review.issueAutoResolveHint": "Закроется автоматически: подтверждены ответы {labels}.",
    "review.irpfPreview": "≈ {amount}",
    "review.technicalDetails": "Технические поля AEAT и центы",
    "review.errorGeneral": "Не удалось применить решение. Проверьте подсвеченные поля.",
    "taxCodeLabels.domestic_output": "Доход в Испании (облагаемый)",
    "taxCodeLabels.domestic_output_zero": "Доход в Испании (нулевая ставка)",
    "taxCodeLabels.eu_goods_income": "Доход: товары в ЕС",
    "taxCodeLabels.eu_service_income": "Доход: услуги в ЕС",
    "taxCodeLabels.export": "Экспорт",
    "taxCodeLabels.outside_scope": "Вне обложения",
    "taxCodeLabels.domestic_input": "Расход в Испании",
    "taxCodeLabels.reverse_charge": "Обратное начисление IVA",
    "taxCodeLabels.withholding_service": "Удержание IRPF: услуги",
    "taxCodeLabels.withholding_rent": "Удержание IRPF: аренда",
    "taxCodeLabels.unknown": "Код не определён",
    "charts.loadError": "Не удалось загрузить аналитику",
    "charts.table": "Данные таблицей",
    "charts.bucket.month": "Месяц",
    "charts.bucket.quarter": "Квартал",
    "charts.empty.noTransactions": "Нет операций за период",
    "charts.empty.missingFx": "У части операций нет курса валюты — суммы не рассчитаны",
    "charts.empty.onlyUnreviewed": "Операции есть, но ещё не разобраны",
    "charts.empty.filedWithoutValues": "Форма подана, но значения не извлечены",
    "charts.business.title": "Доходы и вычитаемые расходы по месяцам",
    "charts.business.aria": "Столбики доходов и вычитаемых расходов по месяцам",
    "charts.business.incomeActual": "Доход — факт",
    "charts.business.incomeBacklog": "Доход — не проведено",
    "charts.business.incomeForecast": "Доход — прогноз",
    "charts.business.expenseActual": "Вычет — факт",
    "charts.business.expenseBacklog": "Вычет — не проведено",
    "charts.business.expenseForecast": "Вычет — прогноз",
    "charts.taxDue.title": "Налоги к оплате по кварталам",
    "charts.taxDue.aria": "Платежи Modelo 130 и Modelo 303 по кварталам",
    "charts.taxDue.m130": "Modelo 130 к оплате",
    "charts.taxDue.m303": "Modelo 303 к оплате",
    "charts.taxDue.empty": "Квартальные расчёты не сформированы",
    "charts.iva.title": "Позиция IVA по кварталам",
    "charts.iva.aria": "IVA начисленный, к вычету и итог по кварталам",
    "charts.iva.output": "IVA начисленный (27)",
    "charts.iva.input": "IVA к вычету (45)",
    "charts.iva.result": "Итог (71)",
    "charts.reserve.title": "Резерв под налоги",
    "charts.reserve.aria": "Требуемый налог, рекомендуемый резерв и доступные средства",
    "charts.reserve.required": "Нужно на налоги",
    "charts.reserve.recommended": "Рекомендуемый резерв",
    "charts.reserve.available": "Доступно",
    "charts.reserve.notChecked": "Свободные средства не проверены — сверьте резерв вручную",
    "charts.reserve.notRequired": "Платежей к резервированию нет",
    "charts.reserve.blocked": "Расчёт заблокирован — резерв не определён",
    "charts.cumulative.title": "Чистый результат нарастающим итогом",
    "charts.cumulative.aria": "Накопленный чистый результат до трудно обосновываемых расходов",
    "charts.cumulative.actual": "Факт",
    "charts.cumulative.projected": "С учётом одобренного",
    "charts.yoy.title": "Год к году (по текущий месяц)",
    "charts.yoy.aria": "Сравнение дохода, вычетов и результата с прошлым годом",
    "charts.yoy.currentYear": "Текущий год",
    "charts.yoy.previousYear": "Прошлый год",
    "charts.yoy.income": "Доход",
    "charts.yoy.deductible": "Вычеты",
    "charts.yoy.net": "Результат",
    "charts.yoy.empty": "Недостаточно данных для сравнения",
    "charts.expenses.title": "Структура расходов по концептам AEAT",
    "charts.expenses.aria": "Расходы по концептам AEAT: вычитаемая и невычитаемая части",
    "charts.expenses.concept": "Концепт",
    "charts.expenses.deductible": "Вычитаемая часть",
    "charts.expenses.nonDeductible": "Невычитаемая часть",
    "charts.expenses.unclassified": "Без концепта",
    "charts.expenses.empty": "Расходы за период не найдены",
    "charts.aging.title": "Очередь разбора по срокам",
    "charts.aging.aria": "Число операций в очереди по возрасту и статусу",
    "charts.aging.bucketLabel": "Дней в очереди",
    "charts.aging.approvedOverdue": "Одобрено, не проведено",
    "charts.aging.empty": "Очередь разбора пуста",
    "charts.counterparties.title": "Крупнейшие клиенты",
    "charts.counterparties.aria": "Доход по крупнейшим контрагентам",
    "charts.counterparties.income": "Доход",
    "charts.counterparties.other": "Прочие",
    "charts.counterparties.noname": "Без контрагента",
    "charts.counterparties.empty": "Доходы за период не найдены",
    "charts.amortization.title": "Амортизация по кварталам",
    "charts.amortization.aria": "Начисления амортизации по кварталам",
    "charts.amortization.perQuarter": "Амортизация за квартал",
    "charts.amortization.note": "На графике — включённые в книги строки по кварталам года. В таблице — выбранный квартал, с отдельными суммами вне книг. Годовые подтверждения не прибавляются.",
    "charts.amortization.empty": "Начислений амортизации нет",
    "charts.expand": "Увеличить",
  },
  en: {
    "expense.purchase": "Purchases, services and other expenses",
    "expense.amortization": "Asset depreciation",
    "expense.explanation": "Part of an earlier asset purchase recognized as an expense this quarter. This is not a new purchase or a payment.",
    "expense.recognitionDate": "Recognition date",
    "expense.sourceDate": "Document dated {date}",
    "expense.sourceAmount": "Document amount: {amount}",
    "expense.recordedAmount": "Source entry amount: {amount}",
    "expense.amount": "Expense amount",
    "expense.quarterAmount": "Quarterly depreciation",
    "expense.periodDate": "Period and recognition date",
    "expense.assetDocument": "Asset / source document",
    "expense.quarter": "Q{quarter} {year}",
    "expense.forQuarter": "Depreciation for {period}",
    "expense.future": "Recognition date has not arrived: {date}",
    "expense.unposted": "Not posted",
    "expense.unmatched": "Asset not matched",
    "expense.inferred": "Matched by counterparty and document date",
    "expense.source": "Source",
    "expense.quarterTotal": "Posted and reviewed for the whole quarter: {amount}",
    "expense.shown": "Showing {shown} of {count}",
    "expense.empty": "No entries in this quarter.",
    "expense.noMatches": "No matching entries.",
    "expense.moreRecords": "These entries are on subsequent pages. Select “Show more”.",
    "expense.loadMore": "Show more",
    "expense.retry": "Retry loading",
    "expense.refreshData": "Refresh data",
    "expense.refreshFailed": "Could not refresh data. Previous values are still shown.",
    "expense.invalidPage": "Could not load the next page. Please retry.",
    "expense.loading": "Loading expenses…",
    "expense.search": "Counterparty, document or asset",
    "expense.posted": "Posted",
    "expense.approved": "Reviewed, not posted: {amount}",
    "expense.futureAmount": "Of which future-dated: {amount}",
    "expense.missing": "Amount information is missing",
    "expense.inAmount": "In depreciation amount",
    "expense.chartSourceNote": "The chart uses source entry amounts. For depreciation this is the original purchase cost, not a new charge; see the section above for the quarterly deduction.",
    "common.loadFailed": "Could not load data. Check the connection and try again.",
    "common.retry": "Retry",
    "common.sessionExpired": "The session expired. Reload the page to restore access.",
    "common.reload": "Reload page",
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
    "nav.settings": "Settings",
    "storage.settings": "SQLite · Settings",
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
    "intake.sourceAria": "Document source",
    "intake.sourceUpload": "Upload file",
    "intake.sourceGoogleDrive": "Google Drive URL",
    "intake.googleDriveUrl": "Google Drive file link",
    "intake.googleDrivePlaceholder": "drive.google.com/file/d/...",
    "intake.googleDriveHint": "The file stays in your archive; the original link is recorded.",
    "intake.googlePickerHint": "Google Picker will be available after narrow drive.file access is configured.",
    "intake.googleDriveInvalid": "Enter a Google Drive file link.",
    "intake.chooseGoogleFolder": "Choose Google Drive folder",
    "intake.googleFolderSelected": "Folder: {name}",
    "intake.googlePickerUnavailable": "Google Drive folder selection is not configured yet.",
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
    "expense.open": "Open expense",
    "expense.title": "Expense details",
    "expense.notes": "Notes",
    "expense.noNotes": "No notes",
    "expense.readOnly": "Read-only saved data",
    "expense.back": "Back to {title} · {period}",
    "expense.notFound": "Expense not found",
    "expense.wrongType": "This is not an expense",
    "expense.refresh": "Refresh data",
    "expense.description": "Description",
    "titles.review": "Review",
    "titles.assets": "Assets",
    "titles.taxes": "Taxes and deadlines",
    "titles.contacts": "Counterparties",
    "titles.settings": "Account settings",
    "contacts.cardTitle": "Counterparty details",
    "contacts.actionsFor": "Actions for {name}",
    "contacts.actions": "Additional actions",
    "contacts.back": "Back to counterparties",
    "contacts.backToParty": "Back to counterparty",
    "contacts.facts": "Details",
    "contacts.operations": "Transactions",
    "contacts.operationType": "Type",
    "contacts.description": "Description",
    "contacts.sourceDocument": "Source document",
    "contacts.email": "Email",
    "contacts.phone": "Phone",
    "contacts.allPeriods": "All periods",
    "contacts.more": "Show more",
    "contacts.shown": "Showing {count} of {total}",
    "contacts.noOperations": "No transactions in this period.",
    "contacts.operationsError": "Could not load transactions.",
    "contacts.retry": "Retry",
    "contacts.notFound": "Counterparty not found.",
    "contacts.refresh": "Refresh counterparty",
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
    "transactions.emptyIncome": "No income in this period yet. Click “Add” to accept an invoice.",
    "transactions.noMatches": "Nothing matches your search.",
    "review.queueEmpty": "The review queue is empty — everything is reviewed.",
    "review.transactions": "Transactions to review",
    "review.documents": "Documents to review",
    "review.openIssues": "Open issues",
    "review.summary": "Posting queue",
    "review.summaryReady": "Can post now",
    "review.summaryNeedsReview": "Needs review",
    "review.category.ready": "can post",
    "review.category.later": "post later",
    "review.category.blocked": "posting blocked",
    "review.category.needs_review": "needs review",
    "reviewTabs.aria": "Review sections",
    "reviewTabs.queue": "Queue",
    "reviewTabs.posting": "Posting",
    "reviewTabs.documents": "Documents and issues",
    "review.submitting": "Submitting…",
    "review.formDisabledHint": "Decision fields are locked while this transaction cannot be posted — the reason is explained above.",
    "intake.draftRestored": "Draft restored — review the fields before accepting.",
    "intake.consistencyHint": "Base + IVA = {expected}, but the total is {total}. Check the amounts.",
    "intake.disabledReason": "Document intake is disabled: the server inbox folders are not configured.",
    "toolbar.periodLocked": "The period is locked while a record card is open.",
    "tables.actions": "Actions",
    "review.summaryLater": "Can post later",
    "review.summaryBlocked": "Blocked after review",
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
    "review.invalidWorkItem": "Could not verify the transaction and its period. Reload the page or return to the list using the Review menu.",
    "review.outcomeApprove": "Approve",
    "review.outcomeReject": "Reject",
    "review.actionResolve": "Resolve issue",
    "review.actionKeepOpen": "Keep open",
    "review.documentLink": "Open file",
    "review.invoiceLabel": "Invoice {number}",
    "review.issueOpen": "Open issue",
    "review.issueResolved": "Issue will be resolved",
    "review.vatInvestment": "IVA purchase classification",
    "review.vatCurrent": "Current purchase for IVA",
    "review.vatAsset": "Investment good for IVA",
    "review.vatUnknown": "IVA classification not reviewed",
    "review.vatInvestmentHint": "IRPF depreciation does not determine IVA classification. Review the purchase separately.",
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
    "assets.decision": "Accounting status",
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
    "contacts.editName": "Correct name",
    "contacts.nameLabel": "Name",
    "contacts.renameHint": "The name will change throughout the app and in new exports. Original documents will remain unchanged.",
    "contacts.manualName": "Manually corrected",
    "contacts.saveName": "Save",
    "contacts.savingName": "Saving…",
    "contacts.nameSaved": "Name corrected",
    "contacts.nameUnchanged": "Name unchanged",
    "contacts.invalidName": "Enter a non-empty, single-line name without control characters.",
    "contacts.nameHistory": "Change history",
    "contacts.noNameHistory": "No manual corrections yet.",
    "contacts.historyError": "Could not load change history.",
    "contacts.retryHistory": "Retry loading",
    "contacts.localActor": "Local session",
    "contacts.sheetActor": "Spreadsheet correction",
    "contacts.nameConflict": "The counterparty changed after this form was opened. Review the current name before saving.",
    "contacts.currentName": "Current name: {name}",
    "contacts.acceptCurrentName": "Reviewed: use the current version",
    "contacts.discardName": "Close without saving the corrected name?",
    "contacts.nameBusy": "The database is temporarily busy. Try saving again.",
    "contacts.nameMissing": "This counterparty is no longer available. Close the form and refresh the list.",
    "contacts.nameRefreshError": "The name was saved, but the list could not refresh. Reopen this section.",
    "refresh.done": "{period} calculation refreshed",
    "documents.counterparty": "Counterparty",
    "documents.type": "Type",
    "issues.none": "No open issues",
    "issues.sourceDetails": "Source details",
    "issues.default": "Manual review is required.",
    "routes.unknownTitle": "Page not found",
    "routes.unknownHint": "This section does not exist. Open the overview to continue.",
    "routes.backToDashboard": "Back to overview",
    "review.factsTitle": "Invoice facts",
    "review.questionsTitle": "What we need from you",
    "review.confirmAction": "Confirm review",
    "review.confirmHint": "The review is applied in one step: the FX rate (when needed) and the invoice decision.",
    "review.confirmSuccess": "Review confirmed, decision applied.",
    "review.rejectAction": "Reject invoice",
    "review.rejectTitle": "Reject invoice",
    "review.rejectLead": "Rejection is a separate action: state whether the document is valid and why.",
    "review.rejectConfirm": "Confirm rejection",
    "review.rejectSuccess": "Invoice rejected.",
    "review.rejectDocumentRequired": "A rejection needs an explicit document-valid decision.",
    "review.rejectReasonRequired": "A rejection needs a reason.",
    "review.fxSuggestionLabel": "ECB / Banco de España reference rate",
    "review.fxExactRate": "Rate for {date}",
    "review.fxPriorRate": "Previous business day rate, {date}",
    "review.fxUseManual": "Documented settlement rate",
    "review.fxExisting": "A rate is already applied to this invoice",
    "review.fxUnavailable": "Reference rate unavailable",
    "review.fxUnavailableHint": "No reference rate for this date. Provide a documented settlement rate with a document reference.",
    "review.fxSettlementRate": "Settlement rate (EUR per {currency})",
    "review.fxSettlementReference": "Settlement document reference",
    "review.fxSettlementDate": "Settlement date",
    "review.fxAmount": "EUR amount",
    "review.fxNeeded": "No FX rate selected",
    "review.fxChoiceNote": "The reference rate is not an actual bank settlement rate.",
    "review.autoFilledTitle": "Filled in automatically",
    "review.autoFilledIntake": "from document intake",
    "review.autoFilledExtraction": "from extracted values",
    "review.autoFilledDefault": "default value",
    "review.issueAutoResolveHint": "Will close automatically: {labels} are confirmed.",
    "review.irpfPreview": "≈ {amount}",
    "review.technicalDetails": "Technical AEAT and cents fields",
    "review.errorGeneral": "The decision could not be applied. Check the highlighted fields.",
    "taxCodeLabels.domestic_output": "Income in Spain (taxable)",
    "taxCodeLabels.domestic_output_zero": "Income in Spain (zero rate)",
    "taxCodeLabels.eu_goods_income": "Income: EU goods",
    "taxCodeLabels.eu_service_income": "Income: EU services",
    "taxCodeLabels.export": "Export",
    "taxCodeLabels.outside_scope": "Outside scope",
    "taxCodeLabels.domestic_input": "Expense in Spain",
    "taxCodeLabels.reverse_charge": "VAT reverse charge",
    "taxCodeLabels.withholding_service": "IRPF withholding: services",
    "taxCodeLabels.withholding_rent": "IRPF withholding: rent",
    "taxCodeLabels.unknown": "Code not set",
    "charts.loadError": "Could not load analytics",
    "charts.table": "Data as a table",
    "charts.bucket.month": "Month",
    "charts.bucket.quarter": "Quarter",
    "charts.empty.noTransactions": "No transactions in this period",
    "charts.empty.missingFx": "Some transactions have no FX rate — amounts are not computed",
    "charts.empty.onlyUnreviewed": "Transactions exist but are not reviewed yet",
    "charts.empty.filedWithoutValues": "The form was filed but no values were extracted",
    "charts.business.title": "Income and deductible expenses by month",
    "charts.business.aria": "Bars of income and deductible expenses by month",
    "charts.business.incomeActual": "Income — actual",
    "charts.business.incomeBacklog": "Income — not posted",
    "charts.business.incomeForecast": "Income — forecast",
    "charts.business.expenseActual": "Deductible — actual",
    "charts.business.expenseBacklog": "Deductible — not posted",
    "charts.business.expenseForecast": "Deductible — forecast",
    "charts.taxDue.title": "Tax cash due by quarter",
    "charts.taxDue.aria": "Modelo 130 and Modelo 303 payments by quarter",
    "charts.taxDue.m130": "Modelo 130 payable",
    "charts.taxDue.m303": "Modelo 303 payable",
    "charts.taxDue.empty": "Quarterly calculations are not available",
    "charts.iva.title": "IVA position by quarter",
    "charts.iva.aria": "Output IVA, deductible input IVA and the result by quarter",
    "charts.iva.output": "Output IVA (27)",
    "charts.iva.input": "Deductible input IVA (45)",
    "charts.iva.result": "Result (71)",
    "charts.reserve.title": "Tax reserve",
    "charts.reserve.aria": "Required tax, recommended reserve and available funds",
    "charts.reserve.required": "Required for taxes",
    "charts.reserve.recommended": "Recommended reserve",
    "charts.reserve.available": "Available",
    "charts.reserve.notChecked": "Available funds are not checked — verify the reserve manually",
    "charts.reserve.notRequired": "No payments to reserve for",
    "charts.reserve.blocked": "Calculation is blocked — the reserve is not determined",
    "charts.cumulative.title": "Cumulative business result",
    "charts.cumulative.aria": "Cumulative net result before difficult-to-justify expenses",
    "charts.cumulative.actual": "Actual",
    "charts.cumulative.projected": "Including approved",
    "charts.yoy.title": "Year over year (through this month)",
    "charts.yoy.aria": "Income, deductibles and result compared with the previous year",
    "charts.yoy.currentYear": "Current year",
    "charts.yoy.previousYear": "Previous year",
    "charts.yoy.income": "Income",
    "charts.yoy.deductible": "Deductibles",
    "charts.yoy.net": "Result",
    "charts.yoy.empty": "Not enough data for a comparison",
    "charts.expenses.title": "Expense structure by AEAT concept",
    "charts.expenses.aria": "Expenses by AEAT concept: deductible and non-deductible parts",
    "charts.expenses.concept": "Concept",
    "charts.expenses.deductible": "Deductible part",
    "charts.expenses.nonDeductible": "Non-deductible part",
    "charts.expenses.unclassified": "No concept",
    "charts.expenses.empty": "No expenses found for the period",
    "charts.aging.title": "Review queue by age",
    "charts.aging.aria": "Queued transactions by age bucket and status",
    "charts.aging.bucketLabel": "Days in queue",
    "charts.aging.approvedOverdue": "Approved, not posted",
    "charts.aging.empty": "The review queue is empty",
    "charts.counterparties.title": "Top customers",
    "charts.counterparties.aria": "Income by largest counterparties",
    "charts.counterparties.income": "Income",
    "charts.counterparties.other": "Other",
    "charts.counterparties.noname": "No counterparty",
    "charts.counterparties.empty": "No income found for the period",
    "charts.amortization.title": "Amortization by quarter",
    "charts.amortization.aria": "Amortization charges by quarter",
    "charts.amortization.perQuarter": "Amortization per quarter",
    "charts.amortization.note": "The chart shows book-included entries across the year. The table shows the selected quarter, separating entries outside the books. Annual evidence is not added.",
    "charts.amortization.empty": "No amortization charges",
    "charts.expand": "Expand",
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
    other: "другой документ",
  },
  en: {
    expense_invoice: "supplier invoice",
    income_invoice: "issued invoice",
    receipt: "receipt",
    bank_statement: "bank statement",
    tax_report: "tax report",
    other_document: "other document",
    other: "other document",
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
  contactDetail: {id: null},
  contactGlobalPeriod: null,
  contactsListPosition: null,
  expenseDetail: {transactionId: null, data: null},
  expensesQuery: "",
  returnTo: null,
  detailPeriodResolved: false,
  intakeKind: "expense_invoice",
  intakeSource: "upload",
  googlePicker: null,
  googleFolder: null,
  copyTargetPeriodKey: null,
  copyTargetIsCurrentQuarter: false,
  locale: loadLocale(),
  review: {
    rows: [],
    issues: [],
    documents: [],
    activeTab: "queue",
    selectedReviewId: null,
    workItem: null,
    validationDirty: true,
    validationResult: null,
    error: "",
    busy: false,
    fxChoice: null,
    confirmError: null,
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
const googleDriveUrl = hasDOM ? document.querySelector("#google-drive-url") : null;
const googleDriveUrlField = hasDOM ? document.querySelector("#google-drive-url-field") : null;
const googlePickerHint = hasDOM ? document.querySelector("#google-picker-hint") : null;
const googleFolderControls = hasDOM ? document.querySelector("#google-folder-controls") : null;
const chooseGoogleFolder = hasDOM ? document.querySelector("#choose-google-folder") : null;
const googleFolderSelection = hasDOM ? document.querySelector("#google-folder-selection") : null;
const intakeNotice = hasDOM ? document.querySelector("#intake-notice") : null;
const intakeStatus = hasDOM ? document.querySelector("#intake-status") : null;
const submitIntake = hasDOM ? document.querySelector("#submit-intake") : null;
const toast = hasDOM ? document.querySelector("#toast") : null;
const toastMessage = hasDOM ? document.querySelector("#toast-message") : null;
const toastClose = hasDOM ? document.querySelector("#toast-close") : null;
const localeButtons = hasDOM ? document.querySelectorAll("[data-locale]") : [];
const postingConfirmDialog = hasDOM ? document.querySelector("#posting-confirm-dialog") : null;
const postingConfirmPeriod = hasDOM ? document.querySelector("#posting-confirm-period") : null;
const postingConfirmBody = hasDOM ? document.querySelector("#posting-confirm-body") : null;
const postingConfirmStatus = hasDOM ? document.querySelector("#posting-confirm-status") : null;
const confirmPostingButton = hasDOM ? document.querySelector("#confirm-posting-button") : null;
const chartDialog = hasDOM ? document.querySelector("#chart-dialog") : null;
const chartDialogTitle = hasDOM ? document.querySelector("#chart-dialog-title") : null;
const chartDialogSlot = hasDOM ? document.querySelector("#chart-dialog-slot") : null;
const chartDialogClose = hasDOM ? document.querySelector("#chart-dialog-close") : null;
let chartDialogEntry = null;
let chartDialogOpener = null;
const incomeCopyRowsById = new Map();
const counterpartyRowsById = new Map();
let counterpartyNameEditor = null;
let counterpartyMenu = null;
let settingsController = null;
let currentRenderGeneration = 0;
let currentReviewRequest = 0;

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

/* ----------------------------- routing ------------------------------ */

const ROUTE_VIEWS = {
  "/dashboard": "dashboard",
  "/income": "income",
  "/expenses": "expenses",
  "/review": "review",
  "/assets": "assets",
  "/taxes": "taxes",
  "/contacts": "contacts",
  "/settings": "settings",
};
const REVIEW_DETAIL_RE = /^\/review\/([0-9a-fA-F-]{32,36})$/;
const EXPENSE_DETAIL_RE = /^\/expenses\/([0-9a-fA-F-]{32,36})$/;
const PERIOD_ROUTE_PATHS = new Set(["/dashboard", "/income", "/expenses", "/review", "/assets", "/taxes"]);

function parseRoute(pathname) {
  const path = String(pathname || "/").split("?")[0].split("#")[0];
  const normalized = path === "" ? "/" : path;
  const contact = /^\/contacts\/([0-9a-fA-F-]{32,36})$/.exec(normalized);
  if (contact) return {view: "contact-detail", reviewId: null, counterpartyId: contact[1].toLowerCase()};
  const detail = REVIEW_DETAIL_RE.exec(normalized);
  if (detail) return {view: "review", reviewId: detail[1]};
  const expense = EXPENSE_DETAIL_RE.exec(normalized);
  if (expense) return {view: "expense-detail", reviewId: null, transactionId: expense[1]};
  if (ROUTE_VIEWS[normalized]) return {view: ROUTE_VIEWS[normalized], reviewId: null};
  return null;
}

function routePathFor(view, reviewId = null) {
  if (view === "contact-detail" && reviewId) return contactUrl(reviewId);
  if (view === "review" && reviewId) return `/review/${encodeURIComponent(String(reviewId).replace(/^transaction:/, ""))}`;
  if (view === "expense-detail" && reviewId) return `/expenses/${encodeURIComponent(reviewId)}`;
  if (view === "review") return "/review";
  return `/${view}`;
}

function buildRouteUrl(view, {period = state.period, reviewId = null, transactionId = null, counterpartyId = null, contactPeriod = "", returnTo = null, q = "", tab = null} = {}) {
  if (view === "contact-detail") return contactUrl(counterpartyId || reviewId, contactPeriod);
  const path = routePathFor(view, transactionId || reviewId);
  const query = new URLSearchParams();
  if ((PERIOD_ROUTE_PATHS.has(`/${view}`) || view === "expense-detail") && period) query.set("period", period);
  if (view === "expenses" && q) query.set("q", q);
  const reviewTab = view === "review" && !reviewId ? (tab || state.review?.activeTab) : null;
  if (reviewTab && reviewTab !== "queue") query.set("tab", reviewTab);
  if (returnTo && (view === "expense-detail" || (view === "review" && reviewId))) query.set("returnTo", returnTo);
  return query.size ? `${path}?${query}` : path;
}

function detailRouteActive(view, id, generation) {
  return generation === currentRenderGeneration && state.view === view
    && (view === "expense-detail" ? state.expenseDetail.transactionId : selectedReviewTransactionId()) === id;
}

function safeReturnUrl(value, period, fallbackView = "expenses") {
  const fallback = buildRouteUrl(fallbackView, {period});
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return fallback;
  try {
    const url = new URL(value, window.location.origin);
    const contact = /^\/contacts\/([0-9a-fA-F-]{32,36})$/.exec(url.pathname);
    if (contact && url.origin === window.location.origin && !url.hash) {
      if ([...url.searchParams.keys()].some(key => key !== "period" || url.searchParams.getAll(key).length !== 1)) return fallback;
      const contactPeriod = url.searchParams.get("period") || "";
      if (contactPeriod && !/^\d{4}-Q[1-4]$/.test(contactPeriod)) return fallback;
      return contactUrl(contact[1].toLowerCase(), contactPeriod);
    }
    if (url.origin !== window.location.origin || url.hash || !["/expenses", "/review", "/dashboard"].includes(url.pathname)) return fallback;
    const allowed = url.pathname === "/expenses" ? ["period", "q"] : ["period"];
    if ([...url.searchParams.keys()].some((key) => !allowed.includes(key) || url.searchParams.getAll(key).length !== 1)) return fallback;
    const sourcePeriod = routePeriodFromQuery(url.search);
    if (!sourcePeriod) return fallback;
    return buildRouteUrl(ROUTE_VIEWS[url.pathname], {period: sourcePeriod, q: url.searchParams.get("q") || ""});
  } catch {
    return fallback;
  }
}

function applyDetailPeriod(data, view, id) {
  const period = data.period.period_key;
  state.period = period;
  state.detailPeriodResolved = true;
  if (!state.bootstrap.periods.some((row) => row.period_key === period)) {
    state.bootstrap.periods.push({period_key: period, status: data.period.status});
    if (periodSelect) periodSelect.innerHTML = state.bootstrap.periods.map((row) => `<option value="${escapeHtml(row.period_key)}">${escapeHtml(row.period_key)}</option>`).join("");
  }
  if (periodSelect) periodSelect.value = period;
  if (state.returnTo) state.returnTo = safeReturnUrl(state.returnTo, period, view === "review" ? "review" : "expenses");
  const url = buildRouteUrl(view, {period, reviewId: view === "review" ? id : null, transactionId: view === "expense-detail" ? id : null, returnTo: state.returnTo});
  if (`${window.location.pathname}${window.location.search}` !== url) window.history.replaceState(window.history.state, "", `${url}${window.location.hash || ""}`);
  applyViewState();
}

function routePeriodFromQuery(search) {
  let value = "";
  try {
    value = new URLSearchParams(String(search || "")).get("period") || "";
  } catch {
    return null;
  }
  const text = String(value || "").trim().toUpperCase();
  if (!text) return null;
  const known = (state.bootstrap?.periods || []).map((row) => row.period_key);
  return known.includes(text) ? text : null;
}

const REVIEW_TABS = ["queue", "posting", "documents"];

function reviewTabFromQuery(query) {
  const value = String(query.get("tab") || "").trim().toLowerCase();
  return REVIEW_TABS.includes(value) ? value : "queue";
}

function unknownRoutePanel() {
  return `
    <div class="empty-state">
      <strong>${escapeHtml(t("routes.unknownTitle"))}</strong>
      <p>${escapeHtml(t("routes.unknownHint"))}</p>
      <a class="primary-button" href="/dashboard" data-spa>${escapeHtml(t("routes.backToDashboard"))}</a>
    </div>`;
}

function selectedReviewTransactionId() {
  const reviewId = state.review.selectedReviewId || "";
  return reviewId.startsWith("transaction:") ? reviewId.slice("transaction:".length) : "";
}

function leaveSettings() {
  if (!settingsController) return true;
  if (!settingsController.canLeave()) return false;
  settingsController.dispose();
  settingsController = null;
  return true;
}

function applyRouteFromLocation() {
  if (!state.bootstrap) return false;
  if (settingsController && !leaveSettings()) {
    window.history.pushState(null, "", "/settings");
    return false;
  }
  closeCounterpartyMenu(false);
  if (counterpartyNameEditor) {
    const edit = counterpartyNameEditor;
    if (!closeCounterpartyNameEditor()) {
      window.history.pushState(edit.pageState, "", edit.pageUrl);
      return false;
    }
  }
  if (hasDOM) closePostingConfirmDialog();
  const route = parseRoute(window.location.pathname);
  const reviewId = route?.view === "review" && route.reviewId
    ? reviewIdFromTransaction(route.reviewId)
    : null;
  if (!reviewId || reviewId !== state.review.selectedReviewId) {
    state.review.workItem = null;
    state.review.fxChoice = null;
    state.review.confirmError = null;
    state.review.validationDirty = true;
    state.review.validationResult = null;
    state.review.error = "";
  }
  state.review.selectedReviewId = reviewId;
  state.expenseDetail = {transactionId: route?.transactionId || null, data: null};
  ++currentReviewRequest;
  if (!route) {
    ++currentRenderGeneration;
    AccountingHelp.beforeRender();
    app.innerHTML = unknownRoutePanel();
    app.setAttribute("aria-busy", "false");
    return false;
  }
  const query = new URLSearchParams(window.location.search);
  if (route.view === "expense-detail" &&
      (state.bootstrap.periods || []).some(item => item.period_key === window.history.state?.contactGlobalPeriod)) {
    state.contactGlobalPeriod = window.history.state.contactGlobalPeriod;
  }
  if (route.view === "contact-detail") {
    const fromContactExpense = state.view === "expense-detail" &&
      safeReturnUrl(state.returnTo, state.period).startsWith("/contacts/");
    const previousGlobal = window.history.state?.contactGlobalPeriod ||
      (state.view === "contact-detail" || fromContactExpense ? state.contactGlobalPeriod : state.period);
    if ((state.bootstrap.periods || []).some(item => item.period_key === previousGlobal)) state.period = previousGlobal;
    state.contactGlobalPeriod = state.period;
    window.history.replaceState({...window.history.state, contactGlobalPeriod: state.period}, "",
      window.location.pathname + window.location.search);
  }
  state.contactDetail = {
    id: route.counterpartyId || null, period: query.get("period") || "",
    data: null, rows: [], nextOffset: 0, total: null, hasMore: false,
    busy: false, error: false, history: null, historyRequest: 0,
  };
  if (PERIOD_ROUTE_PATHS.has(routePathFor(route.view, route.reviewId))) {
    state.period = routePeriodFromQuery(window.location.search)
      || state.period || state.bootstrap.default_period;
    if (state.period) query.set("period", state.period);
  }
  if (route.view === "review" && !route.reviewId) {
    state.review.activeTab = reviewTabFromQuery(query);
    if (state.review.activeTab === "queue") query.delete("tab");
    else query.set("tab", state.review.activeTab);
  } else {
    query.delete("tab");
  }
  const search = query.toString() ? `?${query}` : "";
  if (search !== window.location.search) {
    window.history.replaceState(window.history.state, "",
      `${window.location.pathname}${search}${window.location.hash}`);
  }
  state.expensesQuery = route.view === "expenses" ? query.get("q") || "" : "";
  state.returnTo = (route.reviewId || route.transactionId) ? query.get("returnTo") : null;
  state.detailPeriodResolved = false;
  state.view = route.view;
  applyViewState();
  void renderCurrentView();
  return true;
}

function navigateToUrl(url, {replace = false} = {}) {
  if (!leaveSettings()) return;
  if (counterpartyNameEditor && !closeCounterpartyNameEditor()) return;
  rememberContactsListPosition();
  const nextView = parseRoute(url)?.view;
  let navigationState = null;
  if (state.view === "contact-detail" && nextView === "expense-detail") {
    navigationState = {contactGlobalPeriod: state.contactGlobalPeriod || state.period};
  } else if (state.view === "expense-detail" && nextView === "contact-detail") {
    navigationState = {contactGlobalPeriod: window.history.state?.contactGlobalPeriod || state.contactGlobalPeriod};
  }
  if (replace) window.history.replaceState(navigationState, "", url);
  else window.history.pushState(navigationState, "", url);
  applyRouteFromLocation();
}

function navigateToRoute(view, {replace = false, ...options} = {}) {
  navigateToUrl(buildRouteUrl(view, options), {replace});
}

function handleSpaClick(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  if (!(event.target instanceof Element)) return;
  const link = event.target.closest("a[data-spa]");
  if (!link || link.hasAttribute("target") || link.hasAttribute("download")) return;
  const url = new URL(link.getAttribute("href"), window.location.origin);
  if (url.origin !== window.location.origin || !parseRoute(url.pathname)) return;
  event.preventDefault();
  navigateToUrl(`${url.pathname}${url.search}`);
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
  const projection = workItem?.posting_context || workItem?.ui_context?.posting;
  if (!projection) return {category: "blocked", supported: false, canApply: false,
    availableOn: null, reason: t("review.unknownSupport")};
  const supported = workItem.supported !== false;
  const stateCode = workItem.ui_context?.state || projection.preview_bucket;
  const category = !supported ? "blocked" : stateCode === "deferred" ? "later"
    : ["ready", "blocked", "needs_review"].includes(stateCode) ? stateCode : "blocked";
  return {category, supported, canApply: workItem.review_allowed === true,
    availableOn: projection.posting_deferred_until || null,
    reason: supported ? null : t("review.unsupported")};
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
    const code = row.ui_context?.state || "blocked";
    if (code === "needs_review") summary.needsReview += 1;
    else if (code === "ready") summary.ready += 1;
    else if (code === "deferred") summary.later += 1;
    else summary.blocked += 1;
    return summary;
  }, {needsReview: 0, ready: 0, later: 0, blocked: 0});
}

function formatReviewRowPostingStatus(row, today = todayIso()) {
  const posting = row.ui_context?.posting;
  if (!posting) return t("review.postingBlocked");
  if (posting.preview_bucket === "deferred") return t("review.postingLater", {date: formatDate(posting.posting_deferred_until)});
  if (posting.preview_bucket === "ready") return t("review.postingReady");
  return row.ui_context?.state === "needs_review" ? t("review.needsReview") : t("review.postingBlocked");
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
  if (changed && !leaveSettings()) return;
  state.locale = locale;
  storeLocale(locale);
  applyStaticTranslations();
  if (changed && (state.period || state.view === "settings")) await renderCurrentView();
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

function showApprovedActivityBanner(summary) {
  if (!summary.approvedCount) return "";
  return `
    <div class="period-note posting-banner">
      <div>
        <strong>${escapeHtml(t("dashboard.postingBannerTitle", {count: summary.approvedCount}))}</strong>
        <p>${escapeHtml(t("dashboard.approvedNotPosted"))}</p>
      </div>
      <button type="button" class="secondary-button" data-nav-view="review" data-nav-tab="posting">${escapeHtml(t("dashboard.postingBannerCta"))}</button>
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
  if (value === null || value === undefined || value === "") return "—";
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
    if (!response.ok) {
      const error = new Error(parsed.value?.error || fallbackMessage || `HTTP ${response.status}`);
      error.status = response.status;
      error.code = parsed.value?.code;
      error.current = parsed.value?.current;
      throw error;
    }
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
  (toastMessage || toast).textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.add("visible");
  clearTimeout(showToast.timer);
  if (!error) showToast.timer = setTimeout(() => toast.classList.remove("visible"), 3200);
}

function hideToast() {
  if (!toast) return;
  clearTimeout(showToast.timer);
  toast.classList.remove("visible");
}

function emptyRow(columns, message) {
  return `<tr><td colspan="${columns}"><div class="empty-state">${escapeHtml(message || t("common.noRecords"))}</div></td></tr>`;
}

function errorState(error) {
  const sessionExpired = error.code === "session_forbidden";
  return `<div class="empty-state state-error" role="alert"><p>${escapeHtml(t(sessionExpired ? "common.sessionExpired" : "common.loadFailed"))}</p><button type="button" class="secondary-button" ${sessionExpired ? "data-reload-view" : "data-retry-view"}>${escapeHtml(t(sessionExpired ? "common.reload" : "common.retry"))}</button><details><summary>${escapeHtml(t("issues.sourceDetails"))}</summary><p>${escapeHtml(error.message || String(error))}</p></details></div>`;
}

function uiLoadingSkeleton() {
  return `<div class="loading-state state-loading"><div class="skeleton-panel" aria-hidden="true"><span class="skeleton-line"></span><span class="skeleton-line"></span><span class="skeleton-line"></span></div><p>${escapeHtml(t("common.loading"))}</p></div>`;
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

function chartHostWidth(host) {
  const raw =
    (host && typeof host.getBoundingClientRect === "function"
      ? host.getBoundingClientRect().width
      : 0) ||
    (host && host.clientWidth) ||
    0;
  const width = Math.round(raw) - 28;
  return width > 0 ? width : 0;
}

const viewChartRegistry = new Map();

function badge(value, forcedClass) {
  const text = String(value ?? "unknown");
  const className = forcedClass || text.replace(/[^a-z0-9_-]/gi, "_");
  return `<span class="badge ${escapeHtml(className)}">${escapeHtml(statusLabel(text))}</span>`;
}

const REVIEW_CATEGORY_TONES = {
  ready: "status-positive",
  later: "status-pending",
  blocked: "status-attention",
  needs_review: "status-pending",
};

function reviewCategoryBadge(category) {
  const tone = REVIEW_CATEGORY_TONES[category];
  const label = tone
    ? t(`review.category.${category}`)
    : statusLabel(String(category || "unknown"));
  return `<span class="badge ${tone || "status-neutral"}">${escapeHtml(label)}</span>`;
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

function transactionTable(rows, {copyable = false, sourceUrl = null, emptyMessage = null} = {}) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("transactions.status"))} ${AccountingHelp.term("posting")}</th><th>${escapeHtml(t("transactions.amount"))}</th><th>${escapeHtml(t("transactions.irpfDeduction"))} ${AccountingHelp.term("IRPF")}</th><th>IVA ${AccountingHelp.term("IVA")}</th><th><span class="visually-hidden">${escapeHtml(t("tables.actions"))}</span></th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr data-transaction-id="${escapeHtml(row.transaction_id)}">
              <td>${row.expense_kind === "amortization" ? expenseRecognition(row, true) : formatDate(row.transaction_date)}</td>
              <td class="cell-primary">
                ${row.expense_kind === "amortization" ? expenseAsset(row, sourceUrl) : `
                <strong>${escapeHtml(row.counterparty_name || row.description || t("transactions.noCounterparty"))}</strong>
                <small>${transactionDocumentLink(row, sourceUrl)}</small>
                `}
              </td>
              <td>${row.entry_type === "expense" ? expenseStatus(row) : AccountingHelp.cell(row.ui_context)}</td>
              <td class="amount">${row.expense_kind === "amortization" ? `${expenseMoney(row.deductible_irpf_eur)}<small class="expense-note">${escapeHtml(t("expense.quarterAmount"))}</small>` : row.amount_eur ? eur(row.amount_eur) : `${escapeHtml(row.amount_original || "—")} ${escapeHtml(row.currency || "")}`}</td>
              <td class="amount">${row.expense_kind === "amortization" ? escapeHtml(t("expense.inAmount")) : AccountingHelp.money(row.deductible_irpf_minor)}</td>
              <td class="amount">${AccountingHelp.money(row.deductible_vat_minor)}</td>
              <td>${transactionActions(row, copyable)}</td>
            </tr>`).join("") || emptyRow(7, emptyMessage)}
        </tbody>
      </table>
    </div>`;
}

function hasExpenseAmount(value) {
  return value !== null && value !== undefined && value !== "";
}

function expenseMoney(value) {
  return hasExpenseAmount(value) ? eur(value) : "—";
}

function expenseQuarter(period) {
  const match = /^(\d{4})-Q([1-4])$/.exec(String(period || ""));
  if (!match) return String(period || "—");
  return t("expense.quarter", {
    year: match[1], quarter: state.locale === "ru" ? ["I", "II", "III", "IV"][Number(match[2]) - 1] : match[2],
  });
}

function expenseRecognition(row, amortization = false) {
  const date = escapeHtml(formatDate(row.transaction_date));
  if (amortization) {
    return `<strong>${escapeHtml(expenseQuarter(row.period_key))}</strong><small class="expense-note">${escapeHtml(t("expense.recognitionDate"))}: ${date}</small>`;
  }
  return `${date}${row.document_issued_on && row.document_issued_on !== row.transaction_date
    ? `<small class="expense-note">${escapeHtml(t("expense.sourceDate", {date: formatDate(row.document_issued_on)}))}</small>` : ""}`;
}

function expenseStatus(row) {
  return `${AccountingHelp.cell(row.ui_context)}
    ${row.lifecycle_status === "approved" ? `<small class="expense-note">${escapeHtml(t("expense.unposted"))}</small>` : ""}
    ${row.is_future_dated === true ? `<small class="expense-note">${escapeHtml(t("expense.future", {date: formatDate(row.transaction_date)}))}</small>` : ""}`;
}

function expenseAsset(row, sourceUrl = null) {
  const matched = row.asset_match_count === 1 && row.asset_id;
  const sourceAmount = hasExpenseAmount(row.document_amount_eur) ? row.document_amount_eur : row.amount_eur;
  return `<strong>${escapeHtml(matched ? row.asset_description || t("nav.assets") : t("expense.unmatched"))}</strong>
    <small>${escapeHtml(row.counterparty_name || row.description || "")}</small>
    <small>${transactionDocumentLink(row, sourceUrl)}${row.document_issued_on ? ` · ${escapeHtml(t("expense.sourceDate", {date: formatDate(row.document_issued_on)}))}` : ""}</small>
    <small>${escapeHtml(t(hasExpenseAmount(row.document_amount_eur) ? "expense.sourceAmount" : "expense.recordedAmount", {amount: expenseMoney(sourceAmount)}))}</small>
    ${matched && row.asset_match_method === "inferred" ? `<small>${escapeHtml(t("expense.inferred"))}</small>` : ""}`;
}

function expenseActions(row) {
  const source = row.document_id ? `<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("expense.source"))}</a>` : "";
  const asset = row.asset_match_count === 1 && row.asset_id ? `<a class="text-button" href="/assets" data-spa>${escapeHtml(t("nav.assets"))}</a>` : "";
  return `<div class="transaction-actions">${source}${asset}</div>`;
}

function expenseTable(rows, kind, sourceUrl = null) {
  const amortization = kind === "amortization";
  const headers = amortization
    ? ["expense.assetDocument", "expense.periodDate", "transactions.status", "expense.quarterAmount"]
    : ["expense.recognitionDate", "transactions.counterpartyDocument", "transactions.status", "expense.amount", "transactions.irpfDeduction", "IVA"];
  const labels = headers.map(key => key === "IVA" ? key : t(key));
  return `<div class="table-wrap"><table class="expense-table" role="table">
    <thead role="rowgroup"><tr role="row">${labels.map(label => `<th scope="col" role="columnheader">${escapeHtml(label)}</th>`).join("")}</tr></thead>
    <tbody role="rowgroup">${rows.map(row => {
      const primary = `<strong>${escapeHtml(row.counterparty_name || row.description || t("transactions.noCounterparty"))}</strong><small>${transactionDocumentLink(row, sourceUrl)}</small>
        ${hasExpenseAmount(row.document_amount_eur) && row.document_amount_eur !== row.amount_eur ? `<small>${escapeHtml(t("expense.sourceAmount", {amount: expenseMoney(row.document_amount_eur)}))}</small>` : ""}`;
      const amount = amortization ? row.deductible_irpf_eur : row.amount_eur;
      const amountCell = `<strong>${expenseMoney(amount)}</strong>${!hasExpenseAmount(amount) ? `<small class="expense-note">${escapeHtml(t("expense.missing"))}</small>` : ""}`;
      const cells = amortization
        ? [expenseAsset(row, sourceUrl) + expenseActions(row), expenseRecognition(row, true), expenseStatus(row), amountCell + (hasExpenseAmount(row.deductible_vat_eur) && Number(row.deductible_vat_eur) !== 0 ? `<small class="expense-note">IVA: ${expenseMoney(row.deductible_vat_eur)}</small>` : "")]
        : [expenseRecognition(row), primary + expenseActions(row), expenseStatus(row), amountCell, expenseMoney(row.deductible_irpf_eur), expenseMoney(row.deductible_vat_eur)];
      return `<tr role="row" data-transaction-id="${escapeHtml(row.transaction_id)}">${cells.map((cell, i) => `<td role="cell" data-label="${escapeHtml(labels[i])}" class="${(amortization ? i === 0 : i === 1) ? "expense-primary" : ""}"><div>${cell}</div></td>`).join("")}</tr>`;
    }).join("")}</tbody></table></div>`;
}

function expenseSections(payload, sourceUrl = null) {
  return ["purchase", "amortization"].map(kind => {
    const rows = payload.rows.filter(row => row.expense_kind === kind);
    const matching = payload.matching_counts[kind];
    const total = payload.summary[kind].reviewed_total;
    const emptyKey = matching ? "expense.moreRecords" : payload.period_counts[kind] ? "expense.noMatches" : "expense.empty";
    return `<section class="panel expense-section" aria-labelledby="expenses-${kind}">
      <header class="expense-section-header"><h3 id="expenses-${kind}">${escapeHtml(t(`expense.${kind}`))}</h3>
        <p>${escapeHtml(t("expense.quarterTotal", {amount: expenseMoney(total.amount_eur)}))}</p>
        ${total.missing_amount_count ? `<p>${escapeHtml(t("expense.missing"))}</p>` : ""}
        ${kind === "amortization" ? `<p class="expense-explanation">${escapeHtml(t("expense.explanation"))}</p>` : ""}
        <div class="expense-terms"><span>${escapeHtml(t("transactions.status"))} ${AccountingHelp.term("posting")}</span><span>IRPF ${AccountingHelp.term("IRPF")}</span>${kind === "purchase" ? `<span>IVA ${AccountingHelp.term("IVA")}</span>` : ""}</div>
        <small>${escapeHtml(t("expense.shown", {shown: rows.length, count: matching}))}</small>
      </header>
      ${rows.length ? expenseTable(rows, kind, sourceUrl) : `<div class="empty-state">${escapeHtml(t(emptyKey))}</div>`}
    </section>`;
  }).join("");
}

function expenseMetric(kind, summary) {
  const scopes = summary?.[kind];
  return `<div class="metric"><span>${escapeHtml(t(`expense.${kind}`))}</span>
    <strong>${expenseMoney(scopes?.posted?.amount_eur)}</strong><small>${escapeHtml(t("expense.posted"))}</small>
    <small>${escapeHtml(t("expense.approved", {amount: expenseMoney(scopes?.approved?.amount_eur)}))}</small>
    ${scopes?.future_approved?.count ? `<small>${escapeHtml(t("expense.futureAmount", {amount: expenseMoney(scopes.future_approved.amount_eur)}))}</small>` : ""}
    ${scopes && [scopes.posted, scopes.approved].some(scope => scope.missing_amount_count) ? `<small>${escapeHtml(t("expense.missing"))}</small>` : ""}
  </div>`;
}

function createExpensePager(fetchPage) {
  let version = 0;
  let current = null;
  let currentQuery = "";
  async function load(query, append = false, targetCount = 0) {
    const request = ++version;
    const previous = append && query === currentQuery ? current : null;
    try {
      let offset = previous?.next_offset || 0;
      let rows = previous?.rows || [];
      let snapshot = previous;
      let page;
      do {
        page = await fetchPage({query, offset});
        if (request !== version) return null;
        if (snapshot && (snapshot.as_of !== page.as_of || snapshot.view_revision !== page.view_revision)) {
          return load(query, false, Math.max(targetCount, rows.length + (append ? page.rows.length : 0)));
        }
        snapshot = page;
        rows = [...new Map([...rows, ...page.rows].map(row => [row.transaction_id, row])).values()];
        if (page.has_more && page.next_offset <= offset) throw new Error(t("expense.invalidPage"));
        offset = page.next_offset;
      } while (page.has_more && rows.length < targetCount);
      current = {...page, rows};
      currentQuery = query;
      return current;
    } catch (error) {
      if (request !== version) return null;
      throw error;
    }
  }
  return {
    load,
    refresh: query => load(query, false, query === currentQuery ? current?.rows.length || 0 : 0),
    invalidate: () => { version += 1; },
  };
}

let refreshExpenseView = null;

function replaceExpenseResults(results, html) {
  const focused = document.activeElement;
  const contained = results.contains(focused);
  const id = contained ? focused?.id : null;
  const copyId = contained ? focused?.getAttribute("data-copy-transaction-id") : null;
  const statusHelp = contained ? focused?.getAttribute("data-status-help") : null;
  const statusSubject = contained ? focused?.getAttribute("data-status-subject") : null;
  const term = contained ? focused?.getAttribute("data-help-term") : null;
  const termSection = term ? focused.closest(".expense-section")?.getAttribute("aria-labelledby") : null;
  const href = contained ? focused?.getAttribute("href") : null;
  const transactionId = href || statusHelp ? focused.closest("tr")?.dataset.transactionId : null;
  results.innerHTML = html;
  const replacement = id ? [...results.querySelectorAll("[id]")].find(node => node.id === id)
    : copyId ? [...results.querySelectorAll("[data-copy-transaction-id]")].find(node => node.getAttribute("data-copy-transaction-id") === copyId)
    : statusHelp && statusSubject ? [...results.querySelectorAll("[data-status-help]")].find(node => node.getAttribute("data-status-subject") === statusSubject)
    : statusHelp && transactionId ? [...results.querySelectorAll("[data-status-help]")].find(node => node.closest("tr")?.dataset.transactionId === transactionId)
    : term ? [...results.querySelectorAll("[data-help-term]")].find(node => node.getAttribute("data-help-term") === term
      && node.closest(".expense-section")?.getAttribute("aria-labelledby") === termSection)
    : href ? [...results.querySelectorAll("a")].find(link => link.getAttribute("href") === href
      && link.closest("tr")?.dataset.transactionId === transactionId) : null;
  replacement?.focus({preventScroll: true});
}

async function renderExpenses(renderGeneration = currentRenderGeneration) {
  const token = transactionRenderToken("expense", renderGeneration);
  const period = state.period;
  const active = () => isActiveTransactionRenderToken(token, "expense", renderGeneration);
  app.innerHTML = `<div class="table-toolbar expense-toolbar"><h2>${escapeHtml(t("titles.expenses"))} · ${escapeHtml(state.period)}</h2>
    <div class="toolbar-filters"><label>${escapeHtml(t("expense.search"))}<input id="expense-search" type="search"></label>
    <button class="primary-button" id="view-add-entry">+ ${escapeHtml(t("common.add"))}</button></div></div>
    <div id="expense-results"></div><div id="expense-load-status" role="status" aria-live="polite"></div>
    <button class="secondary-button" id="expense-more" hidden>${escapeHtml(t("expense.loadMore"))}</button>
    <button class="secondary-button" id="expense-retry" hidden>${escapeHtml(t("expense.retry"))}</button>
    <section class="panel"><p class="expense-section-header">${escapeHtml(t("expense.chartSourceNote"))}</p><div class="chart-slot" id="chart-expense-structure"></div></section>`;
  const search = document.querySelector("#expense-search");
  search.value = state.expensesQuery || "";
  const sourceUrl = () => buildRouteUrl("expenses", {period, q: state.expensesQuery});
  const results = document.querySelector("#expense-results");
  const status = document.querySelector("#expense-load-status");
  const more = document.querySelector("#expense-more");
  const retry = document.querySelector("#expense-retry");
  let busy = false;
  let loadVersion = 0;
  let retryAppend = false;
  let retryRefresh = false;
  const pager = createExpensePager(({query, offset}) => fetchJSON(`/api/expenses?period=${encodeURIComponent(period)}&q=${encodeURIComponent(query)}&offset=${offset}`));
  async function load(append = false, refresh = false) {
    if (!active()) return;
    const version = ++loadVersion;
    busy = true;
    retry.hidden = true;
    more.disabled = true;
    status.textContent = t("expense.loading");
    try {
      const page = await (refresh ? pager.refresh(search.value.trim()) : pager.load(search.value.trim(), append));
      if (!page || !active()) return;
      if (refresh && document.querySelector("#status-help-dialog")?.open) return;
      AccountingHelp.beforeRender();
      replaceExpenseResults(results, expenseSections(page, sourceUrl()));
      AccountingHelp.labelTables(results);
      more.hidden = !page.has_more;
      status.textContent = "";
    } catch (error) {
      if (!active()) return;
      if (error.code === "session_forbidden") status.innerHTML = errorState(error);
      else status.textContent = error.message;
      retryAppend = append;
      retryRefresh = refresh;
      retry.hidden = error.code === "session_forbidden";
    } finally {
      if (active() && version === loadVersion) { busy = false; more.disabled = false; }
    }
  }
  const runSearch = debounce(() => { void load(); }, 240);
  search.addEventListener("input", () => {
    if (!active()) return;
    state.expensesQuery = search.value;
    window.history.replaceState(null, "", sourceUrl());
    pager.invalidate();
    loadVersion += 1;
    busy = false;
    results.innerHTML = "";
    more.hidden = true;
    retry.hidden = true;
    status.textContent = t("expense.loading");
    runSearch();
  });
  more.addEventListener("click", () => { void load(true); });
  retry.addEventListener("click", () => { void load(retryAppend, retryRefresh); });
  document.querySelector("#view-add-entry").addEventListener("click", () => openIntake("expense_invoice"));
  refreshExpenseView = () => {
    if (active() && !busy && document.activeElement !== search && !document.querySelector("#status-help-dialog")?.open) void load(false, true);
  };
  await load();
  if (active()) {
    void mountViewAnalyticsChart("chart-expense-structure", buildExpenseStructureSpec, AutonomoCharts.renderHorizontalBars);
  }
}

function transactionDocumentLink(row, sourceUrl) {
  if (row.entry_type !== "expense" || !["posted", "included_in_snapshot"].includes(row.lifecycle_status)) {
    return escapeHtml(row.document_number || row.description || "");
  }
  const url = buildRouteUrl("expense-detail", {transactionId: row.transaction_id, returnTo: sourceUrl});
  return `<a class="expense-document-link" href="${escapeHtml(url)}" data-spa>${escapeHtml(row.document_number || t("expense.open"))}</a>`;
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
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("documents.counterparty"))}</th><th>${escapeHtml(t("fields.number"))}</th><th>${escapeHtml(t("documents.type"))}</th><th>${escapeHtml(t("transactions.status"))} ${AccountingHelp.term("posting")}</th><th>${escapeHtml(t("transactions.amount"))}</th><th><span class="visually-hidden">${escapeHtml(t("tables.actions"))}</span></th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${formatDate(row.issued_on)}</td>
              <td>${escapeHtml(row.counterparty_name || "—")}</td>
              <td>${escapeHtml(row.document_number || "—")}</td>
              <td>${escapeHtml(documentTypeLabel(row.document_type))}</td>
              <td>${AccountingHelp.cell(row.ui_context)}</td>
              <td class="amount">${row.total_eur ? eur(row.total_eur) : "—"}</td>
              <td>${row.source_available ? `<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("common.file"))}</a>` : ""}</td>
            </tr>`).join("") || emptyRow(7)}
        </tbody>
      </table>
    </div>`;
}

function issuesList(rows) {
  if (!rows.length) return `<div class="empty-state">${escapeHtml(t("issues.none"))}</div>`;
  return `<ul class="issues-list">${rows.map(row => `<li>${AccountingHelp.cell(row.ui_context)}</li>`).join("")}</ul>`;
}

function formatMinorEur(minor) {
  return eur(minor / 100);
}

function chartMonthLabel(bucket) {
  const parsed = new Date(`${bucket}-01T12:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(bucket);
  return new Intl.DateTimeFormat(intlLocale(), {month: "short"}).format(parsed);
}

function chartSpecBase(chartId, titleKey, ariaKey, emptyMessage) {
  return {
    chartId,
    title: t(titleKey),
    ariaLabel: t(ariaKey),
    tableLabel: t("charts.table"),
    formatValue: formatMinorEur,
    emptyMessage,
  };
}

function reviewQueueTotal(analytics) {
  const counts = (analytics.datasets.review_aging || {}).counts || {};
  return Object.values(counts).reduce(
    (total, values) => total + values.reduce((sum, value) => sum + value, 0),
    0
  );
}

function transactionsEmptyMessage(analytics) {
  if ((analytics.quality || {}).missing_fx_transaction_count > 0) {
    return t("charts.empty.missingFx");
  }
  if (reviewQueueTotal(analytics) > 0) return t("charts.empty.onlyUnreviewed");
  return t("charts.empty.noTransactions");
}

function taxChartEmptyMessage(points, fallbackKey) {
  const filedWithoutValues = points.some((point) =>
    [point.modelo130, point.modelo303, point].some(
      (entry) => entry && entry.source === "filed_without_values"
    )
  );
  return filedWithoutValues
    ? t("charts.empty.filedWithoutValues")
    : t(fallbackKey);
}

function buildBusinessResultSpec(analytics) {
  const monthly = analytics.datasets.business_result.monthly;
  const spec = chartSpecBase(
    "business-result",
    "charts.business.title",
    "charts.business.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.bucket.month");
  spec.buckets = monthly.buckets.map(chartMonthLabel);
  spec.series = [
    {key: "income-actual", label: t("charts.business.incomeActual"), kind: "bar", stack: "income", tone: "info", pattern: "solid", values: monthly.actual.income_base_minor},
    {key: "income-backlog", label: t("charts.business.incomeBacklog"), kind: "bar", stack: "income", tone: "info", pattern: "hatched", values: monthly.approved_unposted.income_base_minor},
    {key: "income-forecast", label: t("charts.business.incomeForecast"), kind: "bar", stack: "income", tone: "info", pattern: "outline", values: monthly.approved_future.income_base_minor},
    {key: "expense-actual", label: t("charts.business.expenseActual"), kind: "bar", stack: "expense", tone: "warning", pattern: "solid", values: monthly.actual.deductible_expense_minor},
    {key: "expense-backlog", label: t("charts.business.expenseBacklog"), kind: "bar", stack: "expense", tone: "warning", pattern: "hatched", values: monthly.approved_unposted.deductible_expense_minor},
    {key: "expense-forecast", label: t("charts.business.expenseForecast"), kind: "bar", stack: "expense", tone: "warning", pattern: "outline", values: monthly.approved_future.deductible_expense_minor},
  ];
  return spec;
}

function buildTaxDueSpec(analytics) {
  const points = analytics.datasets.quarterly_tax_due.points || [];
  const spec = chartSpecBase(
    "tax-due",
    "charts.taxDue.title",
    "charts.taxDue.aria",
    taxChartEmptyMessage(points, "charts.taxDue.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "m130", label: t("charts.taxDue.m130"), kind: "bar", stack: "m130", tone: "accent", pattern: "solid", values: points.map((point) => point.modelo130.payable_minor)},
    {key: "m303", label: t("charts.taxDue.m303"), kind: "bar", stack: "m303", tone: "warning", pattern: "solid", values: points.map((point) => point.modelo303.payable_minor)},
  ];
  return spec;
}

function buildIvaPositionSpec(analytics) {
  const points = analytics.datasets.iva_position.points || [];
  const spec = chartSpecBase(
    "iva-position",
    "charts.iva.title",
    "charts.iva.aria",
    taxChartEmptyMessage(points, "charts.taxDue.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "output", label: t("charts.iva.output"), kind: "bar", stack: "output", tone: "info", pattern: "solid", values: points.map((point) => point.output_vat_minor)},
    {key: "input", label: t("charts.iva.input"), kind: "bar", stack: "input", tone: "warning", pattern: "solid", values: points.map((point) => point.deductible_input_vat_minor)},
    {key: "result", label: t("charts.iva.result"), kind: "line", tone: "accent", pattern: "solid", values: points.map((point) => point.result_minor)},
  ];
  return spec;
}

function buildReserveSpec(analytics) {
  const reserve = analytics.datasets.reserve_bullet;
  const spec = chartSpecBase(
    "tax-reserve",
    "charts.reserve.title",
    "charts.reserve.aria",
    t("charts.reserve.notRequired")
  );
  spec.bucketLabel = "";
  spec.ranges = [];
  spec.measure = null;
  if (reserve.status === "calculation_blocked" || reserve.status === "unsupported_form") {
    spec.emptyMessage = t("charts.reserve.blocked");
    return spec;
  }
  if (!reserve.required_tax_minor) return spec;
  spec.ranges = [
    {key: "recommended", label: t("charts.reserve.recommended"), tone: "info", value: reserve.recommended_reserve_minor},
    {key: "required", label: t("charts.reserve.required"), tone: "warning", value: reserve.required_tax_minor},
  ];
  spec.measure = {
    key: "available",
    label: t("charts.reserve.available"),
    tone: "accent",
    value: reserve.available_minor,
  };
  spec.unavailableMessage = t("charts.reserve.notChecked");
  return spec;
}

function buildCumulativeNetSpec(analytics) {
  const cumulative = analytics.datasets.cumulative_net;
  const spec = chartSpecBase(
    "cumulative-net",
    "charts.cumulative.title",
    "charts.cumulative.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.bucket.month");
  spec.buckets = cumulative.buckets.map(chartMonthLabel);
  spec.series = [
    {key: "net-projected", label: t("charts.cumulative.projected"), kind: "line", tone: "accent", pattern: "dashed", values: cumulative.projected_minor},
    {key: "net-actual", label: t("charts.cumulative.actual"), kind: "line", tone: "accent", pattern: "solid", values: cumulative.actual_minor},
  ];
  return spec;
}

function buildYearComparisonSpec(analytics) {
  const comparison = analytics.datasets.ytd_comparison;
  const spec = chartSpecBase(
    "ytd-comparison",
    "charts.yoy.title",
    "charts.yoy.aria",
    t("charts.yoy.empty")
  );
  spec.bucketLabel = "";
  spec.buckets = [
    t("charts.yoy.income"),
    t("charts.yoy.deductible"),
    t("charts.yoy.net"),
  ];
  const previous = comparison.previous_year;
  const current = comparison.current_year;
  spec.series = [
    {key: "previous", label: `${t("charts.yoy.previousYear")} (${previous.year})`, kind: "bar", stack: "previous", tone: "warning", pattern: "hatched", values: [previous.taxable_income_minor, previous.deductible_expense_minor, previous.net_minor]},
    {key: "current", label: `${t("charts.yoy.currentYear")} (${current.year})`, kind: "bar", stack: "current", tone: "accent", pattern: "solid", values: [current.taxable_income_minor, current.deductible_expense_minor, current.net_minor]},
  ];
  return spec;
}

function buildExpenseStructureSpec(analytics) {
  const buckets = analytics.datasets.expense_structure.buckets || [];
  const spec = chartSpecBase(
    "expense-structure",
    "charts.expenses.title",
    "charts.expenses.aria",
    transactionsEmptyMessage(analytics)
  );
  spec.bucketLabel = t("charts.expenses.concept");
  spec.rows = buckets.map((bucket) => ({
    key: bucket.concept,
    label:
      bucket.concept === "unclassified"
        ? t("charts.expenses.unclassified")
        : bucket.concept,
    segments: [
      {key: "deductible", label: t("charts.expenses.deductible"), tone: "warning", pattern: "solid", value: bucket.deductible_minor},
      {key: "non-deductible", label: t("charts.expenses.nonDeductible"), tone: "warning", pattern: "hatched", value: bucket.non_deductible_minor},
    ],
  }));
  return spec;
}

function buildReviewAgingSpec(analytics) {
  const aging = analytics.datasets.review_aging;
  const spec = chartSpecBase(
    "review-aging",
    "charts.aging.title",
    "charts.aging.aria",
    t("charts.aging.empty")
  );
  spec.bucketLabel = t("charts.aging.bucketLabel");
  spec.formatValue = (value) => String(value);
  const counts = aging.counts || {};
  const total = reviewQueueTotal(analytics);
  if (!total) {
    spec.rows = [];
    return spec;
  }
  const seriesOrder = [
    {key: "received", label: statusLabel("received"), tone: "info"},
    {key: "extracted", label: statusLabel("extracted"), tone: "warning"},
    {key: "needs_review", label: statusLabel("needs_review"), tone: "accent"},
    {key: "approved_unposted", label: t("charts.aging.approvedOverdue"), tone: "danger"},
  ];
  spec.rows = (aging.buckets || []).map((bucket, index) => ({
    key: bucket,
    label: bucket,
    segments: seriesOrder.map((series) => ({
      key: series.key,
      label: series.label,
      tone: series.tone,
      pattern: "solid",
      value: (counts[series.key] || [])[index] ?? 0,
    })),
  }));
  return spec;
}

function buildCounterpartySpec(analytics) {
  const concentration = analytics.datasets.counterparty_concentration;
  const spec = chartSpecBase(
    "counterparty-concentration",
    "charts.counterparties.title",
    "charts.counterparties.aria",
    t("charts.counterparties.empty")
  );
  spec.bucketLabel = "";
  const rows = (concentration.top || []).map((entry) => ({
    key: String(entry.counterparty_id || "none"),
    label: entry.name || t("charts.counterparties.noname"),
    segments: [
      {key: "income", label: t("charts.counterparties.income"), tone: "info", pattern: "solid", value: entry.income_minor},
    ],
  }));
  if (concentration.other_minor) {
    rows.push({
      key: "other",
      label: t("charts.counterparties.other"),
      segments: [
        {key: "income", label: t("charts.counterparties.income"), tone: "info", pattern: "hatched", value: concentration.other_minor},
      ],
    });
  }
  spec.rows = rows;
  return spec;
}

function buildAmortizationSpec(analytics) {
  const amortization = analytics.datasets.amortization;
  const spec = chartSpecBase(
    "amortization",
    "charts.amortization.title",
    "charts.amortization.aria",
    t("charts.amortization.empty")
  );
  spec.bucketLabel = t("charts.bucket.quarter");
  spec.note = t("charts.amortization.note");
  const points = amortization.points || [];
  spec.buckets = points.map((point) => point.period_key);
  spec.series = [
    {key: "amortization", label: t("charts.amortization.perQuarter"), kind: "bar", stack: "amortization", tone: "accent", pattern: "solid", values: points.map((point) => point.total_minor)},
  ];
  return spec;
}

async function mountViewAnalyticsChart(slotId, buildSpec, renderer) {
  const render = renderer || AutonomoCharts.renderCartesian;
  const generation = currentRenderGeneration;
  try {
    const analytics = await fetchJSON(
      `/api/analytics?period=${encodeURIComponent(state.period)}`
    );
    if (generation !== currentRenderGeneration) return;
    const slot = document.querySelector(`#${slotId}`);
    if (!slot) return;
    const spec = buildSpec(analytics);
    const width = chartHostWidth(slot);
    if (width) spec.width = width;
    const entry = withChartExpandAction({render, spec});
    viewChartRegistry.set(slotId, entry);
    render(slot, spec);
  } catch (error) {
    if (generation !== currentRenderGeneration) return;
    const slot = document.querySelector(`#${slotId}`);
    if (!slot) return;
    const failure = document.createElement("div");
    failure.className = "empty-state chart-empty-state";
    failure.textContent = t("charts.loadError");
    slot.replaceChildren(failure);
  }
}

function openChartDialog(entry, trigger) {
  if (!chartDialog || !chartDialogSlot) return;
  chartDialogEntry = entry;
  chartDialogOpener = trigger || null;
  chartDialogTitle.textContent = entry.spec.title;
  if (!chartDialog.open) chartDialog.showModal();
  renderChartDialogFigure();
  chartDialogClose?.focus();
}

function renderChartDialogFigure() {
  if (!chartDialogEntry || !chartDialogSlot) return;
  const copy = Object.assign({}, chartDialogEntry.spec);
  delete copy.expandAction;
  const width = chartHostWidth(chartDialogSlot);
  if (width) copy.width = width;
  chartDialogEntry.render(chartDialogSlot, copy);
}

function closeChartDialog() {
  if (chartDialog?.open) chartDialog.close();
  chartDialogEntry = null;
  chartDialogOpener = null;
}

function withChartExpandAction(entry) {
  entry.spec.expandAction = {
    label: t("charts.expand"),
    handler: (trigger) => openChartDialog(entry, trigger),
  };
  return entry;
}

function renderDashboardCharts(analyticsResult) {
  const host = document.querySelector("#dashboard-charts");
  if (!host) return;
  if (!analyticsResult.ok) {
    const failure = document.createElement("div");
    failure.className = "empty-state chart-empty-state";
    failure.textContent = t("charts.loadError");
    host.replaceChildren(failure);
    return;
  }
  const analytics = analyticsResult.payload;
  const mount = (slotId, renderChart, spec) => {
    const slot = document.querySelector(`#${slotId}`);
    if (!slot) return;
    const width = chartHostWidth(slot);
    if (width) spec.width = width;
    viewChartRegistry.set(slotId, withChartExpandAction({render: renderChart, spec}));
    renderChart(slot, spec);
  };
  mount("chart-business-result", AutonomoCharts.renderCartesian, buildBusinessResultSpec(analytics));
  mount("chart-tax-due", AutonomoCharts.renderCartesian, buildTaxDueSpec(analytics));
  mount("chart-iva-position", AutonomoCharts.renderCartesian, buildIvaPositionSpec(analytics));
  mount("chart-tax-reserve", AutonomoCharts.renderBullet, buildReserveSpec(analytics));
  mount("chart-cumulative-net", AutonomoCharts.renderCartesian, buildCumulativeNetSpec(analytics));
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
    newEntryButton.title = state.bootstrap.intake_enabled ? "" : t("intake.disabledReason");
    if (window.location.pathname === "/" || window.location.pathname === "") {
      window.history.replaceState(null, "", buildRouteUrl("dashboard"));
    }
    applyRouteFromLocation();
  } catch (error) {
    app.innerHTML = errorState(error);
  }
}

async function renderSettings(generation) {
  const data = await fetchJSON("/api/settings");
  if (generation !== currentRenderGeneration || state.view !== "settings") return;
  settingsController = AutonomoSettings.mount(app, {
    data, locale: state.locale, request: fetchJSON,
    isCurrent: () => generation === currentRenderGeneration && state.view === "settings",
    onProfile: (name) => { state.bootstrap.profile_name = name; profileName.textContent = name; },
    onLocale: changeLocale,
    onReload: () => { if (leaveSettings()) void renderCurrentView(); },
    confirm: (message) => window.confirm(message),
  });
}

async function renderCurrentView() {
  if (!app || (state.view !== "settings" && !state.period && !state.expenseDetail.transactionId && !state.review.selectedReviewId && !state.contactDetail.id)) return;
  closeCounterpartyMenu(false);
  AccountingHelp.beforeRender();
  AccountingHelp.setLocale(state.locale);
  app.setAttribute("aria-busy", "true");
  const renderGeneration = ++currentRenderGeneration;
  refreshCopyTargetState();
  incomeCopyRowsById.clear();
  if (state.view !== "review") closePostingConfirmDialog();
  closeChartDialog();
  viewChartRegistry.clear();
  app.innerHTML = uiLoadingSkeleton();
  try {
    if (state.view === "dashboard") await renderDashboard(renderGeneration);
    if (state.view === "income") await renderTransactions("income", renderGeneration);
    if (state.view === "expenses") await renderExpenses(renderGeneration);
    if (state.view === "expense-detail") await renderExpenseDetail(renderGeneration);
    if (state.view === "review") await renderReview(renderGeneration);
    if (state.view === "assets") await renderAssets(renderGeneration);
    if (state.view === "taxes") await renderTaxes(renderGeneration);
    if (state.view === "contacts") await renderContacts(renderGeneration);
    if (state.view === "contact-detail") await renderContactDetail(renderGeneration);
    if (state.view === "settings") await renderSettings(renderGeneration);
  } catch (error) {
    if (renderGeneration !== currentRenderGeneration) return;
    if (state.view === "expense-detail") {
      if (refreshButton) refreshButton.disabled = false;
      const back = expenseBackLink(safeReturnUrl(state.returnTo, state.period));
      app.innerHTML = `${back}${error.status === 404 ? `<div class="empty-state">${escapeHtml(t("expense.notFound"))}</div>` : errorState(error)}`;
    } else if (state.view === "contact-detail") {
      app.innerHTML = contactBackLink() + (error.status === 404
        ? '<div class="empty-state">' + escapeHtml(t("contacts.notFound")) + '</div>' : errorState(error));
    } else app.innerHTML = errorState(error);
  } finally {
    if (renderGeneration === currentRenderGeneration) { app.setAttribute("aria-busy", "false"); AccountingHelp.labelTables(app); }
  }
}

let dashboardReadVersion = 0;
let dashboardRefreshVersion = 0;

function captureDashboardChartState(container) {
  if (!container.querySelector("#chart-business-result")) return null;
  return {
    scrollX: typeof window === "undefined" ? 0 : window.scrollX,
    scrollY: typeof window === "undefined" ? 0 : window.scrollY,
    slots: [...container.querySelectorAll(".chart-slot")].map(slot => ({
      id: slot.id,
      open: Boolean(slot.querySelector("details")?.open),
      focused: slot.querySelector("summary") === document.activeElement,
    })),
  };
}

function restoreDashboardChartState(container, previous) {
  if (!previous) return;
  const slots = new Map([...container.querySelectorAll(".chart-slot")].map(slot => [slot.id, slot]));
  for (const state of previous.slots) {
    const slot = slots.get(state.id);
    const details = slot?.querySelector("details");
    if (details) details.open = state.open;
    if (state.focused) slot?.querySelector("summary")?.focus({preventScroll: true});
  }
  if (typeof window !== "undefined") window.scrollTo({left: previous.scrollX, top: previous.scrollY, behavior: "auto"});
}

function showDashboardExpenseFailure(error) {
  const notice = document.querySelector("#dashboard-expense-refresh-status");
  const message = document.querySelector("#dashboard-expense-refresh-message");
  if (!notice || !message) return;
  document.querySelector("#dashboard-expense-refresh-retry").hidden = error.code === "session_forbidden";
  if (error.code === "session_forbidden") {
    message.innerHTML = errorState(error);
  } else {
    message.textContent = `${t("expense.refreshFailed")} ${error.message}`;
  }
  notice.hidden = false;
}

async function refreshDashboardExpenses(read = renderDashboard, showFailure = showDashboardExpenseFailure) {
  const version = ++dashboardRefreshVersion;
  const token = transactionRenderToken("dashboard");
  try {
    await read(currentRenderGeneration);
    return true;
  } catch (error) {
    if (version === dashboardRefreshVersion && isActiveTransactionRenderToken(token, "dashboard") && state.view === "dashboard") {
      showFailure(error);
    }
    return false;
  }
}

async function renderDashboard(renderGeneration = currentRenderGeneration) {
  const readVersion = ++dashboardReadVersion;
  const renderToken = transactionRenderToken("dashboard", renderGeneration);
  const analyticsPromise = fetchJSON(
    `/api/analytics?period=${encodeURIComponent(state.period)}`
  )
    .then((payload) => ({ok: true, payload}))
    .catch((error) => ({ok: false, error}));
  const [data, analyticsResult] = await Promise.all([
    fetchJSON(`/api/dashboard?period=${encodeURIComponent(state.period)}`),
    analyticsPromise,
  ]);
  if (!isActiveTransactionRenderToken(renderToken, "dashboard", renderGeneration)) return;
  if (readVersion !== dashboardReadVersion) return;
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

  const chartState = captureDashboardChartState(app);
  if (document.querySelector("#status-help-dialog")?.open) return;
  AccountingHelp.beforeRender();
  replaceExpenseResults(app, `
    <div id="dashboard-expense-refresh-status" class="expense-refresh-status" role="status" aria-live="polite" hidden>
      <div id="dashboard-expense-refresh-message"></div>
      <button class="secondary-button" id="dashboard-expense-refresh-retry">${escapeHtml(t("expense.refreshData"))}</button>
    </div>
    ${showApprovedActivityBanner(postingSummary)}
    <div class="metric-grid">
      ${metric(t("dashboard.incomePosted"), eur(actual.income_eur), countNoun(actual.income_transaction_count, "operations"), "accent")}
      ${expenseMetric("purchase", data.expense_summary)}
      ${expenseMetric("amortization", data.expense_summary)}
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
    <section class="charts-panel" id="dashboard-charts">
      <div class="chart-slot" id="chart-business-result"></div>
      <div class="chart-slot" id="chart-tax-due"></div>
      <div class="chart-slot" id="chart-iva-position"></div>
      <div class="chart-slot" id="chart-tax-reserve"></div>
      <div class="chart-slot" id="chart-cumulative-net"></div>
    </section>
    <div class="dashboard-grid">
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("dashboard.recentTransactions"))}</h2><small>${escapeHtml(state.period)}</small></header>
        ${transactionTable(data.recent_transactions, {copyable: Boolean(state.copyTargetPeriodKey), sourceUrl: buildRouteUrl("dashboard")})}
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("dashboard.needsAttention"))}</h2><small>${data.open_issues.length}</small></header>
        ${issuesList(data.open_issues)}
      </section>
    </div>
  `);
  renderDashboardCharts(analyticsResult);
  restoreDashboardChartState(app, chartState);
  AccountingHelp.labelTables(app);
  document.querySelector("#dashboard-ready-banner")?.addEventListener("click", () => {
    navigateToRoute("review");
  });
  document.querySelector("#dashboard-expense-refresh-retry").addEventListener("click", () => {
    void refreshDashboardExpenses();
  });
}

async function renderTransactions(entryType, renderGeneration = currentRenderGeneration) {
  const renderToken = transactionRenderToken(entryType, renderGeneration);
  let latestSearchRequestId = 0;
  const initialQuery = entryType === "expense" ? state.expensesQuery : "";
  const sourceUrl = () => buildRouteUrl(entryType === "expense" ? "expenses" : "income", {q: state.expensesQuery});
  const rows = await fetchJSON(
    `/api/transactions?period=${encodeURIComponent(state.period)}&entry_type=${entryType}${initialQuery ? `&q=${encodeURIComponent(initialQuery.trim())}` : ""}`
  );
  if (!isActiveTransactionRenderToken(renderToken, entryType, renderGeneration)) return;
  if (entryType === "income") replaceIncomeCopyRows(rows);
  const label = entryType === "income" ? t("titles.income") : t("titles.expenses");
  app.innerHTML = `
    <div class="table-toolbar">
      <h2>${label} · ${escapeHtml(state.period)}</h2>
      <div class="toolbar-filters">
        <input id="transaction-search" type="search" value="${escapeHtml(initialQuery)}" placeholder="${escapeHtml(t("transactions.search"))}">
        <button class="primary-button" id="view-add-entry"><span aria-hidden="true">+</span> ${escapeHtml(t("common.add"))}</button>
      </div>
    </div>
    <section class="panel">
      <div id="transactions-table">${transactionTable(rows, {copyable: entryType === "income" && Boolean(state.copyTargetPeriodKey), sourceUrl: sourceUrl(), emptyMessage: entryType === "income" ? t(initialQuery.trim() ? "transactions.noMatches" : "transactions.emptyIncome") : null})}</div>
    </section>
  `;
  document.querySelector("#view-add-entry").addEventListener("click", () => {
    openIntake(entryType === "income" ? "income_invoice" : "expense_invoice");
  });
  const search = document.querySelector("#transaction-search");
  const searchToken = renderToken;
  search.addEventListener("input", () => {
    ++latestSearchRequestId;
    if (entryType === "expense" && isActiveTransactionRenderToken(searchToken, entryType, renderGeneration)) {
      state.expensesQuery = search.value;
      window.history.replaceState(null, "", sourceUrl());
      document.querySelectorAll(".expense-document-link").forEach((link) => {
        const route = parseRoute(link.getAttribute("href"));
        link.setAttribute("href", buildRouteUrl("expense-detail", {transactionId: route.transactionId, returnTo: sourceUrl()}));
      });
    }
  });
  search.addEventListener("input", debounce(async () => {
    if (!isActiveTransactionRenderToken(searchToken, entryType, renderGeneration)) return;
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
      {copyable: entryType === "income" && Boolean(state.copyTargetPeriodKey), sourceUrl: sourceUrl(), emptyMessage: entryType === "income" ? t(query ? "transactions.noMatches" : "transactions.emptyIncome") : null}
    );
  }, 240));
}

async function fetchTransactionDetail(id) {
  return fetchJSON(`/api/transactions/${encodeURIComponent(id)}`);
}

function expenseBackLink(url) {
  const parsed = new URL(url, window.location.origin);
  if (/^\/contacts\/[0-9a-fA-F-]{32,36}$/.test(parsed.pathname)) {
    return '<a class="secondary-button" href="' + escapeHtml(url) + '" data-spa>' +
      escapeHtml(t("contacts.backToParty")) + '</a>';
  }
  const label = t("expense.back", {title: t(`titles.${ROUTE_VIEWS[parsed.pathname] || "expenses"}`), period: parsed.searchParams.get("period") || "—"});
  return `<a class="secondary-button" href="${escapeHtml(url)}" data-spa>${escapeHtml(label)}</a>`;
}

function expenseDetailMarkup(data, returnUrl) {
  const transaction = data.transaction;
  const period = data.period.period_key;
  const back = expenseBackLink(returnUrl || safeReturnUrl(state.returnTo, period));
  if (transaction.entry_type !== "expense") {
    const url = buildRouteUrl(transaction.entry_type === "income" ? "income" : "dashboard", {period});
    return `${back}<div class="empty-state"><p>${escapeHtml(t("expense.wrongType"))}</p>${expenseBackLink(url)}</div>`;
  }
  const documentState = data.document || {};
  const treatments = data.tax_treatments || [];
  const notes = treatments.filter((row) => row.notes != null && row.notes !== "");
  const treatmentLabel = (row) => `${row.treatment_type} · ${row.jurisdiction}`;
  const minorEur = (value) => value == null ? "—" : eur(value / 100);
  const originalMinor = transaction.amount_original_minor ?? transaction.amount_minor;
  const originalCurrency = transaction.original_currency || transaction.currency;
  const original = originalMinor == null ? "—" : `${new Intl.NumberFormat(intlLocale(), {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(originalMinor / 100)} ${originalCurrency}`;
  const euroMinor = transaction.amount_eur_minor ?? (transaction.currency === "EUR" ? transaction.amount_minor : null);
  const facts = [
    [t("fields.transactionDate"), formatDate(transaction.transaction_date)],
    [t("fields.bookingDate"), formatDate(transaction.booking_date)],
    [t("fields.issuedOn"), formatDate(documentState.issued_on)],
    [t("toolbar.period"), period],
    [t("expense.description"), transaction.description || "—"],
    [t("fields.amount"), original],
    ["EUR", minorEur(euroMinor)],
  ];
  return `<div class="section-stack expense-detail">
    ${data.workflow_follow_up?.follow_up_pending ? `<section class="panel expense-panel-body"><p>${escapeHtml(state.locale === "ru" ? "Расход проведён; обновление расчётов или очистка Inbox ещё не завершены." : "Posted; calculation refresh or Inbox cleanup is pending.")}</p><button type="button" id="expense-follow-up">${escapeHtml(state.locale === "ru" ? "Повторить обновление" : "Retry follow-up")}</button><p id="expense-follow-up-error" role="alert"></p></section>` : ""}
    <header class="review-workspace-header">
      ${back}
      <div><h2>${escapeHtml(documentState.document_number || t("expense.title"))}</h2><p>${escapeHtml(data.counterparty?.display_name || "—")}</p></div>
      <div>${badge(transaction.lifecycle_status)}<p>${escapeHtml(t("expense.readOnly"))}</p></div>
    </header>
    <section class="panel expense-notes" aria-labelledby="expense-notes-title">
      <header class="panel-header"><h2 id="expense-notes-title">${escapeHtml(t("expense.notes"))}</h2></header>
      <div class="expense-panel-body">
      ${notes.length ? notes.map((row) => `<div class="expense-note">${treatments.length > 1 ? `<small>${escapeHtml(treatmentLabel(row))}</small>` : ""}<p class="expense-note-text">${escapeHtml(row.notes)}</p></div>`).join("") : `<p>${escapeHtml(t("expense.noNotes"))}</p>`}
      </div>
    </section>
    <section class="panel">
      <header class="panel-header"><h2>${escapeHtml(t("review.factsTitle"))}</h2><small>${escapeHtml(period)}</small></header>
      <div class="expense-panel-body">
      <dl class="review-facts-list">${facts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>
      ${documentState.document_id ? `<a class="text-button" href="/api/document/${encodeURIComponent(documentState.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("common.file"))}</a>` : ""}
      </div>
    </section>
    <section class="panel">
      <header class="panel-header"><h2>${escapeHtml(t("review.taxDecision"))}</h2></header>
      <div class="expense-panel-body">
      ${treatments.map((row) => `<div class="expense-treatment"><small>${escapeHtml(treatmentLabel(row))}</small><dl class="review-facts-list">
        <div><dt>${escapeHtml(t("fields.taxCode"))}</dt><dd>${escapeHtml(taxCodeLabel(row.tax_code || ""))}</dd></div>
        <div><dt>${escapeHtml(t("transactions.irpfDeduction"))}</dt><dd>${escapeHtml(minorEur(row.deductible_irpf_minor))}</dd></div>
        <div><dt>IVA</dt><dd>${escapeHtml(minorEur(row.deductible_vat_minor))}</dd></div>
        <div><dt>${escapeHtml(t("review.vatInvestment"))}</dt><dd>${escapeHtml(t(row.vat_investment_good == null ? "review.vatUnknown" : row.vat_investment_good ? "review.vatAsset" : "review.vatCurrent"))}</dd></div>
        ${[130, 303, 347].map((form) => `<div><dt>Modelo ${form}</dt><dd>${row[`include_modelo${form}`] == null ? "—" : escapeHtml(boolText(row[`include_modelo${form}`]))}</dd></div>`).join("")}
      </dl></div>`).join("") || `<p>${escapeHtml(t("common.noRecords"))}</p>`}
      </div>
    </section>
  </div>`;
}

async function renderExpenseDetail(renderGeneration = currentRenderGeneration) {
  const id = state.expenseDetail.transactionId;
  const data = await fetchTransactionDetail(id);
  if (!detailRouteActive("expense-detail", id, renderGeneration)) return;
  state.expenseDetail.data = data;
  applyDetailPeriod(data, "expense-detail", id);
  app.innerHTML = expenseDetailMarkup(data, safeReturnUrl(state.returnTo, data.period.period_key));
  app.querySelector("#expense-follow-up")?.addEventListener("click", async (event) => {
    event.target.disabled = true;
    try {
      await fetchJSON(`/api/expense-workflows/${encodeURIComponent(id)}/follow-up`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
      if (detailRouteActive("expense-detail", id, renderGeneration)) await renderExpenseDetail(renderGeneration);
    } catch (error) {
      if (detailRouteActive("expense-detail", id, renderGeneration)) {app.querySelector("#expense-follow-up-error").textContent = error.message;event.target.disabled = false;}
    }
  });
}

function reviewWorkItemPeriod(workItem, reviewId) {
  const packet = workItem?.packet;
  const period = packet?.state?.period?.period_key;
  if (packet?.review_id !== reviewId
    || packet?.state?.transaction?.transaction_id !== reviewIdToTransactionId(reviewId)
    || !(state.bootstrap?.periods || []).some((row) => row.period_key === period)) {
    throw new Error(t("review.invalidWorkItem"));
  }
  return period;
}

async function loadReviewWorkspace(reviewId, {factsOnly = false} = {}) {
  if (state.view !== "review" || state.review.selectedReviewId !== reviewId) return;
  const generation = currentRenderGeneration;
  const requestId = ++currentReviewRequest;
  const isActive = () => generation === currentRenderGeneration
    && requestId === currentReviewRequest
    && state.view === "review" && state.review.selectedReviewId === reviewId;
  try {
    const transactionId = reviewIdToTransactionId(reviewId);
    const data = await fetchTransactionDetail(transactionId);
    if (!isActive()) return;
    if (data.transaction.entry_type === "expense" && ["posted", "included_in_snapshot"].includes(data.transaction.lifecycle_status)) {
      navigateToRoute("expense-detail", {transactionId, period: data.period.period_key, returnTo: state.returnTo, replace: true});
      return;
    }
    if (data.transaction.entry_type === "expense" && typeof ExpenseWorkflow !== "undefined" && !factsOnly) {
      applyDetailPeriod(data, "review", transactionId);
      await ExpenseWorkflow.open({container: app, api: fetchJSON, transactionId, locale: state.locale,
        isActive, taxLabel: taxCodeLabel,
        onLegacy: () => loadReviewWorkspace(reviewId, {factsOnly: true}),
        onPosted: (result) => navigateToRoute("expense-detail", {transactionId: result.transaction_id, period: data.period.period_key, returnTo: state.returnTo})});
      return;
    }
    const workItem = await fetchJSON(`/api/review/work-item?review_id=${encodeURIComponent(reviewId)}`);
    if (!isActive()) return;
    const period = reviewWorkItemPeriod(workItem, reviewId);
    const packet = deepClone(workItem.packet);
    if (packet.state.transaction.entry_type === "expense" && packet.state.assets?.length === 1) {
      packet.decision.asset_decision = "asset";
      packet.decision.asset_id = packet.state.assets[0].asset_id;
    }
    const draft = loadReviewDraft(packet.state.transaction.transaction_id);
    const mode = !factsOnly && draft?.snapshot_hash === packet.snapshot_hash ? "full" : "facts";
    const mergedPacket = mergeReviewDecisionFromDraft(packet, draft, mode);
    state.review.workItem = {
      ...workItem,
      packet: mergedPacket,
      requirements: (workItem.requirements || []).map(normalizeReviewRequirement),
    };
    state.period = period;
    state.review.validationDirty = true;
    state.review.validationResult = null;
    state.review.error = "";
    state.review.fxChoice = initialFxChoice(state.review.workItem.fx_suggestion);
    state.review.confirmError = null;
    persistReviewDraft(mergedPacket, {factsOnly});
    applyDetailPeriod({period: packet.state.period}, "review", transactionId);
    renderReviewWorkspace();
  } catch (error) {
    if (!isActive()) return;
    if (factsOnly && state.review.workItem) {
      state.review.confirmError = {message: error.message, target: "general"};
      renderReviewWorkspace();
    } else {
      app.innerHTML = errorState(error);
    }
  }
}

function reviewTabBar(counts) {
  return `<div class="review-tabs" role="tablist" aria-label="${escapeHtml(t("reviewTabs.aria"))}">${REVIEW_TABS.map((tabId) => `
      <button type="button" role="tab" id="review-tab-${tabId}" aria-selected="${state.review.activeTab === tabId}" aria-controls="review-tabpanel" tabindex="${state.review.activeTab === tabId ? "0" : "-1"}" data-review-tab="${tabId}">${escapeHtml(t(`reviewTabs.${tabId}`))}${counts[tabId] ? `<span class="tab-count">${counts[tabId]}</span>` : ""}</button>`).join("")}
  </div>`;
}

function wireReviewTabFocus() {
  const tablist = document.querySelector(".review-tabs");
  if (!tablist) return;
  tablist.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const tabs = [...tablist.querySelectorAll("[data-review-tab]")];
    const index = tabs.indexOf(document.activeElement);
    if (index === -1) return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : tabs.length - 1;
    tabs[(index + step) % tabs.length].focus();
  });
}

function reviewQueuePanel(summary) {
  return `
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
        <header class="panel-header"><h2>${escapeHtml(t("review.transactions"))}</h2><small>${state.review.rows.length}</small></header>
        ${reviewTransactionTable(state.review.rows)}
      </section>
      <section class="panel">
        <div class="chart-slot" id="chart-review-aging"></div>
      </section>`;
}

function reviewPostingPanel(preview) {
  return `
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
      </section>`;
}

function reviewDocumentsPanel(reviewDocuments) {
  return `
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.documents"))}</h2><small>${reviewDocuments.length}</small></header>
        ${documentTable(reviewDocuments)}
      </section>
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("review.openIssues"))}</h2><small>${state.review.issues.length}</small></header>
        ${issuesList(state.review.issues)}
      </section>`;
}

function renderReviewOverview() {
  const summary = summarizeReviewRows(state.review.rows);
  const preview = currentPostingPreview() || normalizePostingPreview({period: state.period});
  const reviewDocuments = state.review.documents.filter((item) =>
    ["received", "extracted", "needs_review"].includes(item.lifecycle_status)
  );
  if (!REVIEW_TABS.includes(state.review.activeTab)) state.review.activeTab = "queue";
  const activeTab = state.review.activeTab;
  const panels = {
    queue: () => reviewQueuePanel(summary),
    posting: () => reviewPostingPanel(preview),
    documents: () => reviewDocumentsPanel(reviewDocuments),
  };
  app.innerHTML = `
    <div class="section-stack review-shell">
      ${reviewTabBar({queue: summary.needsReview, posting: preview.summary.readyCount, documents: state.review.issues.length})}
      <div class="section-stack" id="review-tabpanel" role="tabpanel" aria-labelledby="review-tab-${activeTab}">
        ${panels[activeTab]()}
      </div>
    </div>
  `;
  wireReviewTabFocus();
  if (activeTab === "queue") {
    mountViewAnalyticsChart(
      "chart-review-aging",
      buildReviewAgingSpec,
      AutonomoCharts.renderHorizontalBars
    );
  }
}

function reviewTransactionTable(rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("review.taxDecision"))}</th><th>${escapeHtml(t("review.result"))}</th><th>${escapeHtml(t("transactions.amount"))}</th><th><span class="visually-hidden">${escapeHtml(t("tables.actions"))}</span></th></tr></thead>
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
                  ${AccountingHelp.cell(row.ui_context)}
                </td>
                <td class="cell-primary">
                  <strong>${escapeHtml(formatReviewRowPostingStatus(row))}</strong>
                  <small>${escapeHtml(row.tax_code ? taxCodeLabel(row.tax_code) : t("taxCodeLabels.unknown"))}</small>
                </td>
                <td class="amount">${row.amount_eur ? eur(row.amount_eur) : `${escapeHtml(row.amount_original || "—")} ${escapeHtml(row.currency || "")}`}</td>
                <td class="table-actions">
                  ${row.document_id ? `<a class="text-button" href="/api/document/${encodeURIComponent(row.document_id)}/content" target="_blank" rel="noreferrer">${escapeHtml(t("review.documentLink"))}</a>` : ""}
                  ${canOpenWorkspace ? `<a class="secondary-button compact-button" href="${escapeHtml(buildRouteUrl("review", {reviewId: row.transaction_id}))}" data-spa data-open-review-id="${escapeHtml(reviewId)}">${escapeHtml(t("review.openWorkspace"))}</a>` : ""}
                </td>
              </tr>`;
          }).join("") || emptyRow(6, t("review.queueEmpty"))}
        </tbody>
      </table>
    </div>`;
}

async function renderReview(renderGeneration = currentRenderGeneration) {
  const reviewId = state.review.selectedReviewId;
  if (reviewId) {
    if (!state.review.workItem || state.review.workItem.packet?.review_id !== reviewId) {
      await loadReviewWorkspace(reviewId);
    } else {
      reviewWorkItemPeriod(state.review.workItem, reviewId);
      applyDetailPeriod({period: state.review.workItem.packet.state.period}, "review", reviewIdToTransactionId(reviewId));
      if (state.review.workItem.packet.state.transaction.entry_type === "expense" && typeof ExpenseWorkflow !== "undefined") {
        await loadReviewWorkspace(reviewId);
      } else {
        renderReviewWorkspace();
      }
    }
    return;
  }
  const period = state.period;
  const [rows, issues, documents, previewPayload] = await Promise.all([
    fetchJSON(`/api/transactions?period=${encodeURIComponent(period)}&status=review`),
    fetchJSON(`/api/issues?period=${encodeURIComponent(period)}`),
    fetchJSON(`/api/documents?period=${encodeURIComponent(period)}`),
    fetchJSON(`/api/review/posting-preview?period=${encodeURIComponent(period)}`),
  ]);
  if (renderGeneration !== currentRenderGeneration || state.view !== "review"
    || state.review.selectedReviewId || period !== state.period) return;
  state.review.rows = rows;
  state.review.issues = issues;
  state.review.documents = documents;
  state.posting.preview = normalizePostingPreview(previewPayload);
  state.posting.previewPeriod = period;
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

/* -------------------------- Guided review --------------------------- */

function taxCodeLabel(code) {
  const value = String(code || "").trim();
  if (!value) return "";
  const key = `taxCodeLabels.${value}`;
  const label = messages[state.locale]?.[key] || messages.ru[key];
  return label ? label : value;
}

function taxCodeSelectOptions(values, selectedValue, includeBlank = true) {
  const options = [];
  if (includeBlank) options.push(`<option value=""></option>`);
  values.forEach((value) => {
    options.push(`<option value="${escapeHtml(value)}"${value === selectedValue ? " selected" : ""}>${escapeHtml(taxCodeLabel(value))}</option>`);
  });
  return options.join("");
}

function questionAnswerMap(decision, reviewState, fxChoice) {
  const text = (value) => String(value || "").trim();
  const counterparty = reviewState?.counterparty || {};
  return {
    business_purpose: Boolean(text(decision?.business_purpose)),
    tax_code: Boolean(decision?.tax_treatment?.tax_code),
    deductible_irpf_minor: decision?.tax_treatment?.deductible_irpf_minor != null,
    asset_decision: Boolean(decision?.asset_decision),
    vat_investment_good: typeof decision?.tax_treatment?.vat_investment_good === "boolean",
    counterparty_country: Boolean(text(decision?.counterparty_changes?.country_code)) ||
      (Boolean(counterparty.country_code) && counterparty.country_code !== "ZZ"),
    fx_rate: Boolean(fxChoice),
    reason: Boolean(text(decision?.reason)),
    document_valid: decision?.document_valid === true || decision?.document_valid === false,
  };
}

function autoResolveCoveredIssues(packet, coverage = {}, answered = {}) {
  const decision = packet?.decision;
  if (!decision || !Array.isArray(decision.issue_resolutions)) return decision;
  const issues = packet?.state?.issues || [];
  decision.issue_resolutions.forEach((resolution, index) => {
    if (!resolution) return;
    const issue = issues[index];
    const covering = coverage?.[issue?.issue_code];
    if (!Array.isArray(covering) || covering.length === 0) return;
    if (covering.every((questionId) => Boolean(answered[questionId]))) {
      resolution.action = "resolve";
      if (!resolution.reason) {
        resolution.reason = t("review.issueAutoResolveHint", {labels: covering.length});
      }
    }
  });
  return decision;
}

function mapConfirmErrorToQuestion(message) {
  const text = String(message || "").toLowerCase();
  if (text.includes("business_purpose") || text.includes("business purpose")) return "business_purpose";
  if (text.includes("tax_code") || text.includes("tax code")) return "tax_code";
  if (text.includes("deductible_irpf")) return "deductible_irpf_minor";
  if (text.includes("counterparty") || text.includes("country")) return "counterparty_country";
  if (text.includes("document_valid") || text.includes("document valid")) return "document_valid";
  if (text.includes("vat_investment_good")) return "vat_investment_good";
  if (text.includes("asset")) return "asset_decision";
  if (text.includes("fx") || text.includes("rate") || text.includes("eur")) return "fx_rate";
  if (text.includes("issue")) return "issues";
  if (text.includes("reason")) return "reason";
  return "general";
}

function inlineErrorFor(questionId) {
  const error = state.review.confirmError;
  if (!error || error.target !== questionId) return "";
  return `<p class="review-inline-error" role="alert">${escapeHtml(error.message)}</p>`;
}

function buildConfirmFxSpec(fxChoice, suggestion) {
  if (!fxChoice) return null;
  if (fxChoice.mode === "ecb" && suggestion) {
    return {
      rate_date: suggestion.rate_date,
      rate: suggestion.eur_per_unit,
      rate_source: "ecb",
      source_reference: suggestion.source_reference,
      raw_observation: suggestion.raw_observation,
      raw_observation_hash: suggestion.raw_observation_hash || null,
      supersedes_rate_id: null,
    };
  }
  const rate = String(fxChoice.rate || "").trim();
  const rateDate = String(fxChoice.rateDate || "").trim();
  const sourceReference = String(fxChoice.sourceReference || "").trim();
  if (!rate || !rateDate || !sourceReference) return null;
  const rawObservation = JSON.stringify({
    kind: "documented_settlement",
    rate,
    rate_date: rateDate,
    source_reference: sourceReference,
  });
  return {
    rate_date: rateDate,
    rate,
    rate_source: "actual_settlement",
    source_reference: sourceReference,
    raw_observation: rawObservation,
    raw_observation_hash: null,
    supersedes_rate_id: null,
  };
}

function fxChoiceNeeded(transaction) {
  if (!transaction) return false;
  const currency = String(transaction.original_currency || transaction.currency || "EUR").toUpperCase();
  return currency !== "EUR" && !transaction.fx_rate_id;
}

function initialFxChoice(suggestion) {
  if (!suggestion) return null;
  if (suggestion.status === "exact" || suggestion.status === "prior") return {mode: "ecb"};
  return null;
}

function renderAutoFilledChips(guidance) {
  const filled = guidance?.auto_filled || {};
  const chips = [];
  if (filled.business_purpose) {
    chips.push({label: t("fields.businessPurpose"), value: String(filled.business_purpose), source: t("review.autoFilledIntake")});
  }
  if (filled.deductible_irpf_minor != null) {
    chips.push({label: t("fields.deductibleIrpfMinor"), value: eur(Number(filled.deductible_irpf_minor) / 100), source: t("review.autoFilledExtraction")});
  }
  if (filled.asset_decision) {
    const label = {
      current_expense: t("review.assetCurrentExpense"),
      asset: t("review.assetAsset"),
      not_applicable: t("review.assetNotApplicable"),
    }[filled.asset_decision];
    if (label) chips.push({label: t("review.assetDecision"), value: label, source: t("review.autoFilledDefault")});
  }
  if (!chips.length) return "";
  return `
    <div class="review-autofilled">
      <span class="review-autofilled-title">${escapeHtml(t("review.autoFilledTitle"))}</span>
      ${chips.map((chip) => `
        <span class="review-autofilled-chip" title="${escapeHtml(chip.source)}">
          <strong>${escapeHtml(chip.label)}:</strong> ${escapeHtml(chip.value)}
          <small>${escapeHtml(chip.source)}</small>
        </span>`).join("")}
    </div>`;
}

function renderSettlementInputs(suggestion, fxChoice) {
  const currency = suggestion?.currency || "USD";
  const choice = fxChoice && fxChoice.mode === "settlement" ? fxChoice : {};
  return `
    <div class="form-grid compact-grid fx-settlement-grid">
      <label>
        <span>${escapeHtml(t("review.fxSettlementRate", {currency}))}</span>
        <input type="number" id="review-fx-settlement-rate" min="0" step="0.00000001" inputmode="decimal" value="${escapeHtml(choice.rate || "")}">
      </label>
      <label>
        <span>${escapeHtml(t("review.fxSettlementDate"))}</span>
        <input type="date" id="review-fx-settlement-date" value="${escapeHtml(choice.rateDate || suggestion?.transaction_date || todayIso())}">
      </label>
      <label class="full-span">
        <span>${escapeHtml(t("review.fxSettlementReference"))}</span>
        <input type="text" id="review-fx-settlement-reference" value="${escapeHtml(choice.sourceReference || "")}">
      </label>
    </div>`;
}

function renderFxCard(suggestion, fxChoice) {
  if (suggestion.status === "existing") {
    return `
      <article class="review-card fx-card fx-card-existing">
        <h3>${escapeHtml(t("review.fx"))}</h3>
        <p class="fx-card-status success">${escapeHtml(t("review.fxExisting"))}</p>
        <dl class="review-facts-list">
          <div><dt>${escapeHtml(t("fields.fxRateDate"))}</dt><dd>${formatDate(suggestion.rate_date)}</dd></div>
          <div><dt>${escapeHtml(t("fields.fxRate"))}</dt><dd>1 ${escapeHtml(suggestion.currency)} = ${escapeHtml(suggestion.eur_per_unit)} EUR</dd></div>
          <div><dt>${escapeHtml(t("fields.fxRateSource"))}</dt><dd>${escapeHtml(suggestion.source_label || suggestion.rate_source || "")}</dd></div>
          ${suggestion.amount_eur ? `<div><dt>${escapeHtml(t("review.fxAmount"))}</dt><dd>${escapeHtml(suggestion.amount_eur)} EUR</dd></div>` : ""}
        </dl>
      </article>`;
  }
  const manualActive = Boolean(fxChoice && fxChoice.mode === "settlement");
  if (suggestion.status === "exact" || suggestion.status === "prior") {
    return `
      <article class="review-card fx-card ${manualActive ? "fx-card-manual" : "fx-card-suggested"}">
        <h3>${escapeHtml(t("review.fx"))}</h3>
        <p class="muted-copy">${escapeHtml(t("review.fxSuggestionLabel"))}</p>
        <label class="fx-choice ${manualActive ? "" : "active"}">
          <input type="radio" name="fx-choice" value="ecb" ${manualActive ? "" : "checked"}>
          <span>
            <strong>${escapeHtml(t(suggestion.status === "exact" ? "review.fxExactRate" : "review.fxPriorRate", {date: formatDate(suggestion.rate_date)}))}</strong>
            <small>1 ${escapeHtml(suggestion.currency)} = ${escapeHtml(suggestion.eur_per_unit)} EUR${suggestion.amount_eur ? ` · ${escapeHtml(t("review.fxAmount"))} ${escapeHtml(suggestion.amount_eur)} EUR` : ""}</small>
          </span>
        </label>
        <label class="fx-choice ${manualActive ? "active" : ""}">
          <input type="radio" name="fx-choice" value="settlement" ${manualActive ? "checked" : ""}>
          <span><strong>${escapeHtml(t("review.fxUseManual"))}</strong></span>
        </label>
        ${manualActive ? renderSettlementInputs(suggestion, fxChoice) : ""}
        <p class="muted-copy fx-choice-note">${escapeHtml(t("review.fxChoiceNote"))}</p>
        ${inlineErrorFor("fx_rate")}
      </article>`;
  }
  const hint = suggestion.note && suggestion.note !== "manual_settlement_required"
    ? String(suggestion.note)
    : t("review.fxUnavailableHint");
  return `
    <article class="review-card fx-card fx-card-unavailable">
      <h3>${escapeHtml(t("review.fx"))}</h3>
      <p class="fx-card-status warning">${escapeHtml(t("review.fxUnavailable"))}</p>
      <p class="muted-copy">${escapeHtml(hint)}</p>
      ${renderSettlementInputs(suggestion, fxChoice)}
      ${inlineErrorFor("fx_rate")}
    </article>`;
}

function renderGuidedIssueCard(issue, index, resolution, guidance) {
  const action = resolution?.action || "";
  const reason = resolution?.reason || "";
  const covering = guidance?.issue_coverage?.[issue.issue_code];
  const answered = questionAnswerMap(
    state.review.workItem?.packet?.decision || {},
    state.review.workItem?.packet?.state || {},
    state.review.fxChoice,
  );
  const autoClosable = Array.isArray(covering) && covering.length > 0 &&
    covering.every((questionId) => Boolean(answered[questionId]));
  return `
    <article class="review-issue-card ${action === "resolve" ? "resolved" : ""} ${autoClosable ? "auto-closable" : ""}">
      <header>
        <strong>${escapeHtml(issueMessage(issue.issue_code))}</strong>
        <span>${escapeHtml(action === "resolve" ? t("review.issueResolved") : t("review.issueOpen"))}</span>
      </header>
      <p>${escapeHtml(issue.message || "")}</p>
      ${autoClosable ? `<p class="review-auto-close-hint">${escapeHtml(t("review.issueAutoResolveHint", {labels: covering.length}))}</p>` : ""}
      <label>
        <span>${escapeHtml(t("fields.reviewAction"))}</span>
        <select data-decision-path="issue_resolutions.${index}.action" data-value-type="nullable-string">
          <option value=""${action === "" ? " selected" : ""}></option>
          <option value="resolve"${action === "resolve" ? " selected" : ""}>${escapeHtml(t("review.actionResolve"))}</option>
        </select>
      </label>
      <label>
        <span>${escapeHtml(t("fields.reviewReason"))}</span>
        <textarea rows="2" data-decision-path="issue_resolutions.${index}.reason">${escapeHtml(reason)}</textarea>
      </label>
      ${inlineErrorFor("issues")}
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
  const fxSuggestion = workItem.fx_suggestion || null;
  const guidance = workItem.guidance || null;
  const evaluation = evaluateWorkItemPosting(workItem);
  const disabledWorkspace = !evaluation.canApply;
  const fxChoice = state.review.fxChoice;
  const sourceHref = documentState.document_id
    ? `/api/document/${encodeURIComponent(documentState.document_id)}/content`
    : "";
  const needsFx = fxChoiceNeeded(transaction) && fxSuggestion && fxSuggestion.status !== "existing";
  const originalAmount = transaction.amount_original_minor != null
    ? `${new Intl.NumberFormat(intlLocale(), {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(transaction.amount_original_minor / 100)} ${escapeHtml(transaction.original_currency || transaction.currency || "")}`
    : "—";
  const counterpartyCountryNeeded = Boolean(counterparty) &&
    ["", "ZZ"].includes(String(counterparty.country_code || ""));

  app.innerHTML = `
    <div class="review-workspace">
      ${AccountingHelp.cell(workItem.ui_context)}
      <div class="review-workspace-header">
        <a class="secondary-button review-back-link" href="${escapeHtml(safeReturnUrl(state.returnTo, state.period, "review"))}" data-spa>${escapeHtml(t("review.workspaceBack"))}</a>
        <div class="review-workspace-title">
          <h2>${escapeHtml(counterparty.display_name || documentState.document_number || t("review.workspaceTitle"))}</h2>
          <p>${escapeHtml(documentState.document_number ? t("review.invoiceLabel", {number: documentState.document_number}) : t("review.workspaceTitle"))}</p>
        </div>
        <div class="review-workspace-status">
          ${reviewCategoryBadge(evaluation.category)}
          <strong>${escapeHtml(postingStatusLabelForWorkItem(workItem))}</strong>
        </div>
      </div>
      ${!evaluation.supported ? `
        <div class="review-alert error" role="alert">
          <strong>${escapeHtml(t("review.unsupported"))}</strong>
          <p>${escapeHtml(evaluation.reason || workItem.unavailable_reason || t("review.unknownSupport"))}</p>
        </div>` : ""}
      ${evaluation.category === "later" ? `
        <div class="review-alert warning" role="alert">
          <strong>${escapeHtml(t("review.summaryLater"))}</strong>
          <p>${escapeHtml(t("review.future", {date: formatDate(evaluation.availableOn)}))}</p>
          <p>${escapeHtml(t("review.formDisabledHint"))}</p>
        </div>` : ""}
      ${evaluation.supported && evaluation.category === "blocked" ? `
        <div class="review-alert warning" role="alert">
          <strong>${escapeHtml(t("review.category.blocked"))}</strong>
          <p>${escapeHtml(evaluation.reason || t("review.formDisabledHint"))}</p>
        </div>` : ""}
      ${state.review.confirmError?.target === "general" ? `
        <div class="review-alert error" role="alert">${escapeHtml(state.review.confirmError.message)}</div>` : ""}
      ${!evaluation.canApply && evaluation.availableOn ? `<p class="review-alert warning">${escapeHtml(t("review.future", {date: formatDate(evaluation.availableOn)}))}</p>` : ""}
      <form id="review-form" class="review-form">
        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.factsTitle"))}</h2><small>${escapeHtml(state.period)}</small></header>
          <div class="review-facts-summary">
            <dl class="review-facts-list">
              <div><dt>${escapeHtml(t("fields.transactionDate"))}</dt><dd>${formatDate(transaction.transaction_date)}</dd></div>
              <div><dt>${escapeHtml(t("transactions.counterpartyDocument"))}</dt><dd>${escapeHtml(counterparty.display_name || "—")} · ${escapeHtml(documentState.document_number || "—")}</dd></div>
              <div><dt>${escapeHtml(t("fields.currency"))}</dt><dd>${escapeHtml(transaction.original_currency || transaction.currency || documentState.currency || "EUR")}</dd></div>
              <div><dt>${escapeHtml(t("fields.amount"))}</dt><dd>${escapeHtml(originalAmount)}${transaction.amount_eur_minor != null ? ` → ${escapeHtml(eur(transaction.amount_eur_minor / 100))}` : ""}</dd></div>
              <div><dt>${escapeHtml(t("fields.issuedOn"))}</dt><dd>${formatDate(documentState.issued_on)}</dd></div>
            </dl>
            ${renderAutoFilledChips(guidance)}
            ${sourceHref ? `<a class="text-button" href="${escapeHtml(sourceHref)}" target="_blank" rel="noreferrer">${escapeHtml(t("review.documentLink"))}</a>` : ""}
          </div>
        </section>

        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.questionsTitle"))}</h2><small>${escapeHtml(transaction.entry_type || "invoice")}</small></header>
          <div class="review-guided-grid">
            <label class="review-guided-field">
              <span>${escapeHtml(t("fields.businessPurpose"))}</span>
              <textarea rows="2" data-decision-path="business_purpose">${escapeHtml(decision.business_purpose || "")}</textarea>
              ${inlineErrorFor("business_purpose")}
            </label>
            ${transaction.entry_type === "expense" ? `
              <label class="review-guided-field">
                <span>${escapeHtml(t("review.assetDecision"))}</span>
                <select data-decision-path="asset_decision">
                  <option value=""></option>
                  <option value="current_expense"${decision.asset_decision === "current_expense" ? " selected" : ""}>${escapeHtml(t("review.assetCurrentExpense"))}</option>
                  <option value="asset"${decision.asset_decision === "asset" ? " selected" : ""}>${escapeHtml(t("review.assetAsset"))}</option>
                </select>
                ${inlineErrorFor("asset_decision")}
              </label>
              <label class="review-guided-field">
                <span>${escapeHtml(t("review.vatInvestment"))}</span>
                <select data-decision-path="tax_treatment.vat_investment_good" data-value-type="nullable-boolean" aria-describedby="vat-investment-help">
                  <option value=""${decision.tax_treatment?.vat_investment_good == null ? " selected" : ""}></option>
                  <option value="false"${decision.tax_treatment?.vat_investment_good === false ? " selected" : ""}>${escapeHtml(t("review.vatCurrent"))}</option>
                  <option value="true"${decision.tax_treatment?.vat_investment_good === true ? " selected" : ""}>${escapeHtml(t("review.vatAsset"))}</option>
                </select>
                <small id="vat-investment-help">${escapeHtml(t("review.vatInvestmentHint"))}</small>
                ${inlineErrorFor("vat_investment_good")}
              </label>` : ""}
            ${transaction.entry_type === "expense" ? `
              <label class="review-guided-field">
                <span>${escapeHtml(t("fields.deductibleIrpfMinor"))} ${minorUnitEurPreview(decision.tax_treatment?.deductible_irpf_minor)}</span>
                <input type="number" inputmode="numeric" data-decision-path="tax_treatment.deductible_irpf_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.deductible_irpf_minor ?? "")}">
                ${inlineErrorFor("deductible_irpf_minor")}
              </label>` : ""}
            <label class="review-guided-field">
              <span>${escapeHtml(t("fields.taxCode"))}</span>
              <select data-decision-path="tax_treatment.tax_code">
                ${taxCodeSelectOptions(packet.allowed_values?.tax_code || Array.from(KNOWN_REVIEW_TAX_CODES), decision.tax_treatment?.tax_code || "")}
              </select>
              ${inlineErrorFor("tax_code")}
            </label>
            ${counterpartyCountryNeeded ? `
              <label class="review-guided-field">
                <span>${escapeHtml(t("fields.counterpartyCountry"))}</span>
                <input type="text" data-decision-path="counterparty_changes.country_code" value="${escapeHtml(decision.counterparty_changes?.country_code || counterparty.country_code || "")}">
                ${inlineErrorFor("counterparty_country")}
              </label>` : ""}
            <label class="review-guided-field full-span">
              <span>${escapeHtml(t("fields.reason"))}</span>
              <textarea rows="2" data-decision-path="reason">${escapeHtml(decision.reason || "")}</textarea>
              ${inlineErrorFor("reason")}
            </label>
          </div>
          ${fxSuggestion ? renderFxCard(fxSuggestion, fxChoice) : ""}
          ${needsFx && !fxChoice ? `<p class="review-inline-error fx-needed" role="alert">${escapeHtml(t("review.fxNeeded"))}</p>` : ""}
          ${issues.length ? `
            <div class="review-issues-grid">
              ${issues.map((issue, index) => renderGuidedIssueCard(issue, index, decision.issue_resolutions?.[index], guidance)).join("")}
            </div>` : ""}
          <details class="review-technical-details">
            <summary>${escapeHtml(t("review.technicalDetails"))}</summary>
            <div class="form-grid compact-grid">
              <label>
                <span>${escapeHtml(t("fields.invoiceType"))}</span>
                <select data-decision-path="tax_treatment.aeat_invoice_type">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_invoice_type || [], decision.tax_treatment?.aeat_invoice_type || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.operationKey"))}</span>
                <input type="text" data-decision-path="tax_treatment.aeat_operation_key" value="${escapeHtml(decision.tax_treatment?.aeat_operation_key || "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.operationQualification"))}</span>
                <select data-decision-path="tax_treatment.aeat_operation_qualification" data-value-type="nullable-string">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_operation_qualification || [], decision.tax_treatment?.aeat_operation_qualification || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.exemptionCode"))}</span>
                <select data-decision-path="tax_treatment.aeat_exemption_code" data-value-type="nullable-string">
                  ${reviewSelectOptions(packet.allowed_values?.aeat_exemption_code || [], decision.tax_treatment?.aeat_exemption_code || "", true)}
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.reverseCharge"))}</span>
                <select data-decision-path="tax_treatment.aeat_reverse_charge" data-value-type="nullable-boolean">
                  <option value=""${decision.tax_treatment?.aeat_reverse_charge == null ? " selected" : ""}></option>
                  <option value="true"${decision.tax_treatment?.aeat_reverse_charge === true ? " selected" : ""}>${escapeHtml(t("common.yes"))}</option>
                  <option value="false"${decision.tax_treatment?.aeat_reverse_charge === false ? " selected" : ""}>${escapeHtml(t("common.no"))}</option>
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.expenseConcept"))}</span>
                <input type="text" data-decision-path="tax_treatment.aeat_expense_concept" data-value-type="nullable-string" value="${escapeHtml(decision.tax_treatment?.aeat_expense_concept || "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.taxableBaseMinor"))} ${minorUnitEurPreview(decision.tax_treatment?.taxable_base_minor)}</span>
                <input type="number" inputmode="numeric" data-decision-path="tax_treatment.taxable_base_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.taxable_base_minor ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.vatMinor"))} ${minorUnitEurPreview(decision.tax_treatment?.vat_minor)}</span>
                <input type="number" inputmode="numeric" data-decision-path="tax_treatment.vat_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.vat_minor ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.deductibleVatMinor"))} ${minorUnitEurPreview(decision.tax_treatment?.deductible_vat_minor)}</span>
                <input type="number" inputmode="numeric" data-decision-path="tax_treatment.deductible_vat_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.deductible_vat_minor ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.withholdingMinor"))} ${minorUnitEurPreview(decision.tax_treatment?.withholding_minor)}</span>
                <input type="number" inputmode="numeric" data-decision-path="tax_treatment.withholding_minor" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.withholding_minor ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.deductibleRatio"))}</span>
                <input type="number" min="0" max="1" step="0.01" inputmode="decimal" data-decision-path="tax_treatment.deductible_ratio" data-value-type="number" value="${escapeHtml(decision.tax_treatment?.deductible_ratio ?? "")}">
              </label>
              <label>
                <span>${escapeHtml(t("fields.rateBasisPoints"))}</span>
                <input type="number" min="0" inputmode="numeric" data-decision-path="tax_treatment.rate_basis_points" data-value-type="integer" value="${escapeHtml(decision.tax_treatment?.rate_basis_points ?? "")}">
              </label>
              <label class="checkbox-label">
                <input type="checkbox" ${Boolean(decision.tax_treatment?.include_modelo130) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo130" data-value-type="boolean">
                <span>${escapeHtml(t("fields.includeModelo130"))}</span>
              </label>
              <label class="checkbox-label">
                <input type="checkbox" ${Boolean(decision.tax_treatment?.include_modelo303) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo303" data-value-type="boolean">
                <span>${escapeHtml(t("fields.includeModelo303"))}</span>
              </label>
              <label class="checkbox-label">
                <input type="checkbox" ${Boolean(decision.tax_treatment?.include_modelo347) ? "checked" : ""} data-decision-path="tax_treatment.include_modelo347" data-value-type="boolean">
                <span>${escapeHtml(t("fields.includeModelo347"))}</span>
              </label>
              <label class="full-span">
                <span>${escapeHtml(t("fields.notes"))}</span>
                <textarea rows="2" data-decision-path="tax_treatment.notes">${escapeHtml(decision.tax_treatment?.notes || "")}</textarea>
              </label>
              ${counterparty ? `
                <label>
                  <span>${escapeHtml(t("fields.taxId"))}</span>
                  <input type="text" data-decision-path="counterparty_changes.tax_id" data-value-type="nullable-string" value="${escapeHtml(decision.counterparty_changes?.tax_id ?? counterparty.tax_id ?? "")}">
                </label>
                <label>
                  <span>${escapeHtml(t("fields.vatId"))}</span>
                  <input type="text" data-decision-path="counterparty_changes.vat_id" data-value-type="nullable-string" value="${escapeHtml(decision.counterparty_changes?.vat_id ?? counterparty.vat_id ?? "")}">
                </label>
                <label>
                  <span>${escapeHtml(t("fields.roiStatus"))}</span>
                  <select data-decision-path="counterparty_changes.roi_status" data-value-type="nullable-string">
                    ${reviewSelectOptions(Object.keys(roiStatusLabels), decision.counterparty_changes?.roi_status ?? counterparty.roi_status ?? "unknown")}
                  </select>
                </label>
                <label>
                  <span>${escapeHtml(t("fields.legalForm"))}</span>
                  <select data-decision-path="counterparty_changes.legal_form" data-value-type="nullable-string">
                    ${reviewSelectOptions(Object.keys(legalFormLabels), decision.counterparty_changes?.legal_form ?? counterparty.legal_form ?? "unknown")}
                  </select>
                </label>` : ""}
            </div>
          </details>
        </section>

        <section class="panel review-panel">
          <header class="panel-header"><h2>${escapeHtml(t("review.result"))}</h2><small>${escapeHtml(t("review.confirmHint"))}</small></header>
          <details class="review-reject-panel">
            <summary>${escapeHtml(t("review.rejectAction"))}</summary>
            <div class="review-reject-body">
              <p class="muted-copy">${escapeHtml(t("review.rejectLead"))}</p>
              <label>
                <span>${escapeHtml(t("fields.documentValid"))}</span>
                <select id="review-reject-document-valid">
                  <option value=""></option>
                  <option value="true">${escapeHtml(t("common.yes"))}</option>
                  <option value="false">${escapeHtml(t("common.no"))}</option>
                </select>
              </label>
              <label>
                <span>${escapeHtml(t("fields.reason"))}</span>
                <textarea rows="2" id="review-reject-reason">${escapeHtml(decision.reason || "")}</textarea>
              </label>
              ${inlineErrorFor("reject")}
              <div class="review-reject-actions">
                <button type="button" class="danger-button" id="review-reject-button">${escapeHtml(t("review.rejectConfirm"))}</button>
              </div>
            </div>
          </details>
          <div class="review-actions">
            <button type="button" class="secondary-button" id="review-refresh-button">${escapeHtml(t("common.refresh"))}</button>
            <button type="submit" class="primary-button" id="review-primary-button"${disabledWorkspace ? ' disabled data-locked="true"' : ""}>${escapeHtml(t("review.confirmAction"))}</button>
          </div>
        </section>
      </form>
    </div>
  `;

  document.querySelector("#review-refresh-button")?.addEventListener("click", () => {
    void loadReviewWorkspace(packet.review_id, {factsOnly: true});
  });

  if (disabledWorkspace) {
    document.querySelectorAll("#review-form input, #review-form select, #review-form textarea").forEach((element) => {
      if (!element.closest(".review-reject-panel")) element.disabled = true;
    });
  }

  document.querySelectorAll("[data-decision-path]").forEach((element) => {
    const eventName = element.tagName === "SELECT" || element.type === "checkbox" ? "change" : "input";
    const eurPreview = element.closest("label")?.querySelector("[data-eur-preview]");
    element.addEventListener(eventName, () => {
      updateReviewDecision(element.dataset.decisionPath, readDecisionFieldValue(element));
      state.review.confirmError = null;
      if (eurPreview) eurPreview.textContent = minorUnitEurPreviewText(element.value);
    });
  });

  document.querySelectorAll("input[name='fx-choice']").forEach((radio) => {
    radio.addEventListener("change", () => {
      const suggestion = state.review.workItem?.fx_suggestion;
      state.review.fxChoice = radio.value === "settlement"
        ? {mode: "settlement", rate: "", rateDate: suggestion?.transaction_date || todayIso(), sourceReference: ""}
        : initialFxChoice(suggestion);
      state.review.confirmError = null;
      renderReviewWorkspace();
    });
  });

  const syncSettlementInputs = () => {
    const rateEl = document.querySelector("#review-fx-settlement-rate");
    const dateEl = document.querySelector("#review-fx-settlement-date");
    const refEl = document.querySelector("#review-fx-settlement-reference");
    if (!rateEl || !dateEl || !refEl) return;
    state.review.fxChoice = {
      mode: "settlement",
      rate: rateEl.value.trim(),
      rateDate: dateEl.value,
      sourceReference: refEl.value.trim(),
    };
    state.review.confirmError = null;
  };
  document.querySelector("#review-fx-settlement-rate")?.addEventListener("input", syncSettlementInputs);
  document.querySelector("#review-fx-settlement-date")?.addEventListener("input", syncSettlementInputs);
  document.querySelector("#review-fx-settlement-reference")?.addEventListener("input", syncSettlementInputs);

  document.querySelector("#review-reject-button")?.addEventListener("click", () => {
    void submitReviewReject();
  });

  document.querySelector("#review-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitReviewConfirm();
  });
}

function minorUnitEurPreviewText(value) {
  if (value === null || value === undefined || String(value).trim() === "") return "";
  const minor = Number.parseInt(String(value), 10);
  if (!Number.isFinite(minor)) return "";
  return t("review.irpfPreview", {amount: eur(minor / 100)});
}

function minorUnitEurPreview(value) {
  return `<output class="review-irpf-preview" data-eur-preview>${escapeHtml(minorUnitEurPreviewText(value))}</output>`;
}

function setReviewSubmitBusy(busy) {
  const submit = document.querySelector("#review-primary-button");
  const reject = document.querySelector("#review-reject-button");
  if (submit) {
    submit.disabled = busy || Boolean(submit.dataset.locked);
    submit.textContent = t(busy ? "review.submitting" : "review.confirmAction");
  }
  if (reject) {
    reject.disabled = busy;
    reject.textContent = t(busy ? "review.submitting" : "review.rejectConfirm");
  }
}

async function submitReviewConfirm() {
  const packet = currentReviewPacket();
  if (!packet || state.review.busy) return;
  const generation = currentRenderGeneration;
  const workItem = state.review.workItem;
  const transaction = packet.state?.transaction || {};
  const suggestion = workItem?.fx_suggestion || null;
  if (fxChoiceNeeded(transaction)) {
    const fxSpec = buildConfirmFxSpec(state.review.fxChoice, suggestion);
    if (!fxSpec) {
      state.review.confirmError = {message: t("review.fxNeeded"), target: "fx_rate"};
      renderReviewWorkspace();
      return;
    }
  }
  const decision = packet.decision;
  decision.outcome = "approve";
  decision.document_valid = true;
  if (decision.counterparty_changes == null) decision.counterparty_changes = {};
  autoResolveCoveredIssues(
    packet,
    workItem?.guidance?.issue_coverage || {},
    questionAnswerMap(decision, packet.state, state.review.fxChoice),
  );
  const fxSpec = fxChoiceNeeded(transaction) ? buildConfirmFxSpec(state.review.fxChoice, suggestion) : null;
  state.review.busy = true;
  state.review.confirmError = null;
  setReviewSubmitBusy(true);
  try {
    await fetchJSON("/api/review/confirm", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({packet, fx: fxSpec}),
    });
    clearReviewDraft(transaction.transaction_id);
    state.review.busy = false;
    setReviewSubmitBusy(false);
    if (!detailRouteActive("review", transaction.transaction_id, generation)) return;
    showToast(t("review.confirmSuccess"));
    state.review.selectedReviewId = null;
    state.review.workItem = null;
    state.review.fxChoice = null;
    navigateToRoute("review");
  } catch (error) {
    state.review.busy = false;
    setReviewSubmitBusy(false);
    if (!detailRouteActive("review", transaction.transaction_id, generation)) return;
    state.review.confirmError = {message: error.message, target: mapConfirmErrorToQuestion(error.message)};
    renderReviewWorkspace();
  }
}

async function submitReviewReject() {
  const packet = currentReviewPacket();
  if (!packet || state.review.busy) return;
  const generation = currentRenderGeneration;
  const transactionId = packet.state.transaction.transaction_id;
  const documentValidRaw = document.querySelector("#review-reject-document-valid")?.value || "";
  const reasonRaw = (document.querySelector("#review-reject-reason")?.value || "").trim();
  if (documentValidRaw !== "true" && documentValidRaw !== "false") {
    state.review.confirmError = {message: t("review.rejectDocumentRequired"), target: "reject"};
    renderReviewWorkspace();
    return;
  }
  if (!reasonRaw) {
    state.review.confirmError = {message: t("review.rejectReasonRequired"), target: "reject"};
    renderReviewWorkspace();
    return;
  }
  const decision = packet.decision;
  decision.outcome = "reject";
  decision.document_valid = documentValidRaw === "true";
  decision.reason = reasonRaw;
  decision.counterparty_changes = {};
  decision.tax_treatment = {...(decision.tax_treatment || {}), tax_code: null};
  state.review.busy = true;
  state.review.confirmError = null;
  setReviewSubmitBusy(true);
  try {
    await fetchJSON("/api/review/confirm", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({packet, fx: null}),
    });
    clearReviewDraft(transactionId);
    state.review.busy = false;
    setReviewSubmitBusy(false);
    if (!detailRouteActive("review", transactionId, generation)) return;
    showToast(t("review.rejectSuccess"));
    state.review.selectedReviewId = null;
    state.review.workItem = null;
    state.review.fxChoice = null;
    navigateToRoute("review");
  } catch (error) {
    state.review.busy = false;
    setReviewSubmitBusy(false);
    if (!detailRouteActive("review", transactionId, generation)) return;
    state.review.confirmError = {message: error.message, target: "reject"};
    renderReviewWorkspace();
  }
}

async function renderAssets(renderGeneration = currentRenderGeneration, selectedAssetId = null, postingResult = null) {
  const rows = await fetchJSON(`/api/assets?period=${encodeURIComponent(state.period)}`);
  if (renderGeneration !== currentRenderGeneration || state.view !== "assets") return;
  app.innerHTML = `
    <div class="table-toolbar"><div><h2>${escapeHtml(t("assets.title"))}</h2><p>${escapeHtml(AccountingHelp.word("allAssets"))}: ${escapeHtml(state.period)}</p></div></div>
    <section class="panel"><div class="table-wrap"><table class="asset-explanations-table">
      <thead><tr><th>${escapeHtml(t("assets.asset"))}</th><th>${escapeHtml(t("assets.inService"))}</th><th>${escapeHtml(t("assets.cost"))} ${AccountingHelp.term("cost")} / ${escapeHtml(t("assets.base"))} ${AccountingHelp.term("base")}</th><th>${escapeHtml(t("assets.businessUse"))} ${AccountingHelp.term("use")} / ${escapeHtml(t("assets.rate"))} ${AccountingHelp.term("rate")}</th><th>${escapeHtml(AccountingHelp.word("scope"))} ${escapeHtml(state.period)} ${AccountingHelp.term("forecast")}</th><th>${escapeHtml(t("assets.decision"))}</th></tr></thead>
      <tbody>${rows.map(row => `<tr>
        <td class="cell-primary" data-label="${escapeHtml(t("assets.asset"))}"><strong>${escapeHtml(row.description || row.asset_code)}</strong><small>${escapeHtml(row.source_invoice_number || "")}</small></td>
        <td data-label="${escapeHtml(t("assets.inService"))}">${formatDate(row.placed_in_service_on)}</td>
        <td data-label="${escapeHtml(t("assets.cost"))}">${AccountingHelp.money(row.cost_minor)}<small class="value-detail">${escapeHtml(t("assets.base"))}: ${AccountingHelp.money(row.amortizable_base_minor)}</small></td>
        <td data-label="${escapeHtml(t("assets.businessUse"))}">${row.business_use_percent == null ? "—" : escapeHtml(row.business_use_percent)+"%"}<small class="value-detail">${escapeHtml(t("assets.rate"))}: ${row.annual_rate_percent == null ? "—" : escapeHtml(row.annual_rate_percent)+"%"}</small></td>
        <td data-label="${escapeHtml(AccountingHelp.word("scope"))}">${AccountingHelp.schedule(row)}</td>
        <td data-label="${escapeHtml(t("assets.decision"))}">${AccountingHelp.cell(row.ui_context)}</td>
      </tr>`).join("") || emptyRow(6)}</tbody></table></div></section>
    <section class="panel">
      <div class="chart-slot" id="chart-amortization"></div>
    </section>`;
  if (typeof ExpenseWorkflow !== "undefined") {
    const actions = document.createElement("section");
    actions.className = "panel wf-fields";
    actions.innerHTML = `<label><span>${escapeHtml(state.locale === "ru" ? "График оборудования" : "Equipment schedule")}</span><select id="asset-plan-select"><option value=""></option>${rows.map((row) => `<option value="${escapeHtml(row.asset_id)}">${escapeHtml(row.description || row.asset_code)}</option>`).join("")}</select></label><div id="asset-plan-detail"></div>`;
    app.appendChild(actions);
    const selector = actions.querySelector("select");
    async function loadSelectedSchedule() {
      const assetId = selector.value;
      if (!assetId) {actions.querySelector("#asset-plan-detail").innerHTML = "";return;}
      try {
        await ExpenseWorkflow.showSchedule({container: actions.querySelector("#asset-plan-detail"), api: fetchJSON,
          assetId, locale: state.locale, isActive: () => renderGeneration === currentRenderGeneration && state.view === "assets" && selector.value === assetId,
          onPosted: (result) => renderAssets(renderGeneration, assetId, result)});
      } catch (error) {if (renderGeneration === currentRenderGeneration) actions.querySelector("#asset-plan-detail").textContent = error.message;}
    }
    selector.addEventListener("change", loadSelectedSchedule);
    if (selectedAssetId) {
      selector.value = selectedAssetId;
      await loadSelectedSchedule();
      const status = actions.querySelector('[role="status"]');
      if (status && postingResult) status.textContent = postingResult.follow_up_pending
        ? (state.locale === "ru" ? "Проведено; требуется обновление расчётов." : "Posted; calculation refresh needs retry.")
        : (state.locale === "ru" ? "Амортизация проведена." : "Depreciation posted.");
    }
  }
  mountViewAnalyticsChart("chart-amortization", buildAmortizationSpec);
}

async function renderTaxes(renderGeneration = currentRenderGeneration) {
  const data = await fetchJSON(`/api/taxes?period=${encodeURIComponent(state.period)}`);
  if (renderGeneration !== currentRenderGeneration || state.view !== "taxes") return;
  const obligations = obligationMap(data.obligations);
  const m130 = formCardData(data.tax_forms?.[FORM_KEYS[130]], obligations[130]);
  const m303 = formCardData(data.tax_forms?.[FORM_KEYS[303]], obligations[303]);
  app.innerHTML = `
    <div class="tax-layout">
      <section class="panel">
        <header class="panel-header"><h2>${escapeHtml(t("taxes.obligations"))}</h2><small>${escapeHtml(data.period)}</small></header>
        <div class="table-wrap">
          <table>
            <thead><tr><th>${escapeHtml(t("taxes.form"))}</th><th>${escapeHtml(t("taxes.applicability"))}</th><th>${escapeHtml(t("taxes.status"))}</th><th>${escapeHtml(t("taxes.directDebit"))} ${AccountingHelp.term("paymentDeadline")}</th><th>${escapeHtml(t("taxes.deadline"))}</th></tr></thead>
            <tbody>
              ${data.obligations.map((row) => `
                <tr>
                  <td><strong>Modelo ${escapeHtml(row.obligation_code)}</strong></td>
                  <td>${escapeHtml(row.determination === "due" ? (state.locale === "ru" ? "Обязательна" : "Required") : statusLabel(row.determination))}</td>
                  <td>${AccountingHelp.cell(row.ui_context)}</td>
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
          ${calculationHelp(m130)}
        </section>
        <section class="panel">
          <header class="panel-header"><h2>Modelo 303</h2><small>${escapeHtml(formSubtitle(m303, obligations[303]))}</small></header>
          ${casillas(m303.values || {}, ["29", "45", "64", "69", "71", "72", "result", "compensation_carryforward"], formEmptyState(m303))}
          ${calculationHelp(m303)}
        </section>
        <section class="panel">
          <div class="chart-slot" id="chart-ytd-comparison"></div>
        </section>
      </div>
    </div>
  `;
  mountViewAnalyticsChart("chart-ytd-comparison", buildYearComparisonSpec);
}

async function renderContacts(renderGeneration = currentRenderGeneration) {
  const rows = await fetchJSON("/api/counterparties");
  if (renderGeneration !== currentRenderGeneration || state.view !== "contacts") return;
  counterpartyRowsById.clear();
  rows.forEach((row) => counterpartyRowsById.set(row.counterparty_id, row));
  app.innerHTML = `
    <div class="table-toolbar"><h2>${escapeHtml(t("contacts.title"))}</h2></div>
    <section class="panel">
      <div class="table-wrap" id="contacts-list-wrap">
        <table>
          <thead><tr><th>${escapeHtml(t("contacts.name"))}</th><th>${escapeHtml(t("contacts.country"))}</th><th>NIF / VAT ID</th><th>ROI ${AccountingHelp.term("ROI")}</th><th>${escapeHtml(t("contacts.transactions"))}</th><th>${escapeHtml(t("contacts.last"))}</th><th class="counterparty-action-cell"><span class="sr-only">${escapeHtml(t("contacts.actions"))}</span></th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr data-counterparty-row="${escapeHtml(row.counterparty_id)}" class="counterparty-row">
                <td class="cell-primary">${counterpartyNameCell(row)}</td>
                <td>${escapeHtml(row.country_code || "—")}</td>
                <td>${escapeHtml(row.vat_id || row.tax_id || "—")}</td>
                <td>${AccountingHelp.cell(row.ui_context)}</td>
                <td>${row.transaction_count}</td>
                <td>${formatDate(row.last_transaction_on)}</td>
                <td class="counterparty-action-cell">${counterpartyMenuTrigger(row)}</td>
              </tr>`).join("") || emptyRow(7)}
          </tbody>
        </table>
      </div>
    </section>
    <section class="panel">
      <div class="chart-slot" id="chart-counterparty-concentration"></div>
    </section>
  `;
  mountViewAnalyticsChart(
    "chart-counterparty-concentration",
    buildCounterpartySpec,
    AutonomoCharts.renderHorizontalBars
  );
  restoreContactsListPosition();
}

function contactUrl(id, period = "") {
  const path = "/contacts/" + encodeURIComponent(id);
  return period ? path + "?period=" + encodeURIComponent(period) : path;
}

function contactBackLink() {
  return '<a class="text-button contact-back-link" href="/contacts" data-spa><span aria-hidden="true">←</span> ' +
    escapeHtml(t("contacts.back")) + '</a>';
}

function counterpartyMenuTrigger(row) {
  return '<button type="button" class="counterparty-more" data-counterparty-menu="' +
    escapeHtml(row.counterparty_id) + '" aria-label="' + escapeHtml(t("contacts.actionsFor", {name: row.display_name})) +
    '" aria-haspopup="menu" aria-expanded="false" aria-controls="counterparty-actions-menu"><span aria-hidden="true">⋯</span></button>';
}

function contactMenuPosition(rect, width, height, viewportWidth, viewportHeight) {
  return {
    left: Math.max(8, Math.min(rect.right - width, viewportWidth - width - 8)),
    top: Math.max(8, rect.bottom + height + 4 <= viewportHeight - 8 ? rect.bottom + 4 : rect.top - height - 4),
  };
}

function closeCounterpartyMenu(restoreFocus = true) {
  if (!counterpartyMenu) return;
  const trigger = counterpartyMenu.trigger;
  counterpartyMenu = null;
  document.querySelector("#counterparty-actions-menu").hidden = true;
  trigger.setAttribute("aria-expanded", "false");
  if (restoreFocus && trigger.isConnected) trigger.focus({preventScroll: true});
}

function toggleCounterpartyMenu(trigger) {
  if (counterpartyMenu?.trigger === trigger) { closeCounterpartyMenu(); return; }
  closeCounterpartyMenu(false);
  const id = trigger.dataset.counterpartyMenu;
  const row = state.view === "contact-detail" && state.contactDetail.id === id
    ? state.contactDetail.data?.counterparty : counterpartyRowsById.get(id);
  if (!row) return;
  const menu = document.querySelector("#counterparty-actions-menu");
  counterpartyMenu = {trigger, row: {...row}};
  trigger.setAttribute("aria-expanded", "true");
  menu.hidden = false;
  const position = contactMenuPosition(trigger.getBoundingClientRect(), menu.offsetWidth,
    menu.offsetHeight, window.innerWidth, window.innerHeight);
  menu.style.left = position.left + "px";
  menu.style.top = position.top + "px";
  menu.querySelector('[role="menuitem"]').focus({preventScroll: true});
}

function counterpartyRowClickAllowed(event) {
  return !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey &&
    !event.altKey && !event.shiftKey && !window.getSelection()?.toString() &&
    !event.target.closest("a, button, input, select, textarea, summary, [role=button], [contenteditable]");
}

function closeCounterpartyMenuFromOutside(target) {
  if (!counterpartyMenu || target.closest("#counterparty-actions-menu") || counterpartyMenu.trigger.contains(target)) return;
  // Do not steal focus from the input/link the user deliberately clicked.
  closeCounterpartyMenu(!target.closest("a, button, input, select, textarea, [tabindex], [contenteditable]"));
}

function rememberContactsListPosition() {
  if (!hasDOM || state.view !== "contacts") return;
  const wrapper = document.querySelector("#contacts-list-wrap");
  if (!wrapper) return;
  const position = {
    x: window.scrollX, y: window.scrollY, tableX: wrapper.scrollLeft,
    id: document.activeElement?.closest("[data-counterparty-row]")?.dataset.counterpartyRow || null,
  };
  state.contactsListPosition = position;
  window.history.replaceState({...window.history.state, contactsList: position}, "", window.location.href);
}

function restoreContactsListPosition() {
  const position = window.history.state?.contactsList || state.contactsListPosition;
  if (!position) return;
  const wrapper = document.querySelector("#contacts-list-wrap");
  if (wrapper) wrapper.scrollLeft = position.tableX;
  Array.from(document.querySelectorAll("[data-counterparty-row]"))
    .find(row => row.dataset.counterpartyRow === position.id)?.querySelector("a")?.focus({preventScroll: true});
  window.scrollTo({left: position.x, top: position.y, behavior: "auto"});
}

function activeContactDetail(detail, generation) {
  return state.view === "contact-detail" && state.contactDetail === detail &&
    currentRenderGeneration === generation;
}

function contactIdentityMarkup(party) {
  const facts = [
    [t("contacts.country"), party.country_code === "ZZ" ? null : party.country_code],
    ["NIF", party.tax_id], ["VAT ID", party.vat_id],
    [t("fields.legalForm"), legalFormLabels[party.legal_form || "unknown"]?.[state.locale] || party.legal_form],
    [t("contacts.email"), party.email], [t("contacts.phone"), party.phone],
  ];
  return '<header class="contact-detail-header"><h2>' + escapeHtml(party.display_name) +
    '</h2>' + counterpartyMenuTrigger(party) + '</header><section class="panel contact-facts-panel">' +
    '<h3>' + escapeHtml(t("contacts.facts")) + '</h3><dl class="contact-facts">' +
    facts.map(([label, value]) => '<div><dt>' + escapeHtml(label) + '</dt><dd>' + escapeHtml(value || "—") + '</dd></div>').join("") +
    '<div><dt>' + escapeHtml(t("fields.roiStatus")) + '</dt><dd>' + AccountingHelp.cell(party.ui_context) +
    '</dd></div></dl></section>';
}

function contactOperationMarkup(row, detail) {
  const description = row.entry_type === "expense"
    ? '<a data-spa href="' + escapeHtml(buildRouteUrl("expense-detail", {
        transactionId: row.transaction_id, period: row.period_key, returnTo: contactUrl(detail.id, detail.period),
      })) + '">' + escapeHtml(row.description || t("expense.title")) + '</a>'
    : escapeHtml(row.description || "—");
  const document = (row.ui_context?.documents || []).find(item => item.document_id === row.document_id);
  const source = document?.available
    ? '<a target="_blank" rel="noreferrer" href="/api/document/' + encodeURIComponent(row.document_id) +
      '/content">' + escapeHtml(row.document_number || t("common.file")) + '</a>'
    : escapeHtml(row.document_number || "—");
  const amount = row.expense_kind === "amortization"
    ? expenseMoney(row.deductible_irpf_eur) + '<small class="expense-note">' + escapeHtml(t("expense.quarterAmount")) + '</small>'
    : hasExpenseAmount(row.amount_eur) ? eur(row.amount_eur)
    : escapeHtml(row.amount_original ?? "—") + " " + escapeHtml(row.currency || "");
  const cells = [
    ["transactions.date", formatDate(row.transaction_date), ""],
    ["contacts.operationType", escapeHtml(row.entry_type === "income" ? t("nav.income") : row.entry_type === "expense" ? t("nav.expenses") : row.entry_type), ""],
    ["contacts.description", description, "cell-primary"], ["contacts.sourceDocument", source, ""],
    ["transactions.status", row.entry_type === "expense" ? expenseStatus(row) : AccountingHelp.cell(row.ui_context), ""],
    ["transactions.amount", amount, "amount"],
  ];
  return '<tr>' + cells.map(([key, content, className]) => '<td class="' + className +
    '" data-label="' + escapeHtml(t(key)) + '">' + content + '</td>').join("") + '</tr>';
}

function renderContactOperations(detail) {
  const host = document.querySelector("#contact-operations-results");
  if (!host) return;
  const columns = ["transactions.date", "contacts.operationType", "contacts.description",
    "contacts.sourceDocument", "transactions.status", "transactions.amount"];
  host.innerHTML = (detail.rows.length ? '<div class="table-wrap"><table><thead><tr>' +
    columns.map(key => '<th>' + escapeHtml(t(key)) + '</th>').join("") + '</tr></thead><tbody>' +
    detail.rows.map(row => contactOperationMarkup(row, detail)).join("") + '</tbody></table></div>' : "") +
    (detail.error ? '<p role="alert">' + escapeHtml(t("contacts.operationsError")) + '</p>'
      : detail.busy && !detail.rows.length ? '<p role="status">' + escapeHtml(t("common.loading")) + '</p>'
      : !detail.rows.length ? '<p>' + escapeHtml(t("contacts.noOperations")) + '</p>' : "") +
    '<div class="contact-page-actions">' +
    (detail.total !== null ? '<small>' + escapeHtml(t("contacts.shown", {count: detail.rows.length, total: detail.total})) + '</small>' : "") +
    ((detail.error || detail.hasMore) ? '<button type="button" class="secondary-button" id="contact-load-more"' +
      (detail.busy ? " disabled" : "") + '>' + escapeHtml(t(detail.busy ? "common.loading" : detail.error ? "contacts.retry" : "contacts.more")) + '</button>' : "") +
    '</div>';
  AccountingHelp.labelTables(host);
}

async function loadContactOperations(detail, generation) {
  if (detail.busy || !activeContactDetail(detail, generation)) return;
  detail.busy = true;
  detail.error = false;
  renderContactOperations(detail);
  try {
    const query = new URLSearchParams({offset: String(detail.nextOffset), limit: "50"});
    if (detail.period) query.set("period", detail.period);
    const page = await fetchJSON("/api/counterparties/" + encodeURIComponent(detail.id) + "/transactions?" + query);
    if (!activeContactDetail(detail, generation)) return;
    const ids = new Set(detail.rows.map(row => row.transaction_id));
    detail.rows.push(...page.rows.filter(row => !ids.has(row.transaction_id)));
    detail.nextOffset = page.next_offset;
    detail.total = page.matching_count;
    detail.hasMore = page.has_more;
  } catch (_) {
    if (activeContactDetail(detail, generation)) detail.error = true;
  } finally {
    if (activeContactDetail(detail, generation)) {
      detail.busy = false;
      renderContactOperations(detail);
    }
  }
}

function counterpartyHistoryMarkup(changes) {
  if (!changes.length) return '<p>' + escapeHtml(t("contacts.noNameHistory")) + '</p>';
  return '<ol class="counterparty-name-history-list">' + changes.map(change => {
    const actor = change.actor || t(change.change_source === "sheet" ? "contacts.sheetActor" : "contacts.localActor");
    const time = new Intl.DateTimeFormat(intlLocale(), {dateStyle: "medium", timeStyle: "short"}).format(new Date(change.changed_at));
    return '<li><div>' + escapeHtml(change.old_name) + ' → <strong>' + escapeHtml(change.new_name) +
      '</strong></div><small>' + escapeHtml(time) + ' · ' + escapeHtml(actor) + '</small></li>';
  }).join("") + '</ol>';
}

async function loadContactHistory(detail, generation) {
  const request = ++detail.historyRequest;
  const body = document.querySelector("#contact-history-body");
  body.textContent = t("common.loading");
  try {
    const result = await fetchJSON("/api/counterparties/" + encodeURIComponent(detail.id) + "/name-history");
    if (!activeContactDetail(detail, generation) || request !== detail.historyRequest) return;
    detail.history = result.changes;
    body.innerHTML = counterpartyHistoryMarkup(result.changes);
  } catch (_) {
    if (!activeContactDetail(detail, generation) || request !== detail.historyRequest) return;
    body.innerHTML = '<p role="alert">' + escapeHtml(t("contacts.historyError")) +
      '</p><button class="secondary-button" type="button" id="contact-retry-history">' + escapeHtml(t("contacts.retry")) + '</button>';
  }
}

async function renderContactDetail(generation) {
  const detail = state.contactDetail;
  const data = await fetchJSON("/api/counterparties/" + encodeURIComponent(detail.id));
  if (!activeContactDetail(detail, generation)) return;
  detail.data = data;
  detail.rows = [];
  detail.nextOffset = 0;
  detail.total = null;
  detail.hasMore = false;
  detail.busy = false;
  detail.history = null;
  const periods = [...new Set([detail.period, ...data.periods].filter(Boolean))];
  app.innerHTML = '<div class="section-stack contact-detail">' + contactBackLink() +
    '<div id="contact-identity">' + contactIdentityMarkup(data.counterparty) + '</div>' +
    '<section class="panel contact-operations"><header class="panel-header"><h3>' + escapeHtml(t("contacts.operations")) +
    '</h3><label class="contact-period-label" for="contact-period">' + escapeHtml(t("toolbar.period")) +
    '<select id="contact-period" aria-label="' + escapeHtml(t("toolbar.period")) + '"><option value="">' + escapeHtml(t("contacts.allPeriods")) + '</option>' +
    periods.map(period => '<option value="' + escapeHtml(period) + '"' + (period === detail.period ? " selected" : "") +
      '>' + escapeHtml(period) + '</option>').join("") + '</select></label></header>' +
    '<div id="contact-operations-results" class="contact-panel-body"></div></section>' +
    '<details class="panel contact-history" id="contact-history"><summary>' + escapeHtml(t("contacts.nameHistory")) +
    '</summary><div id="contact-history-body" class="contact-panel-body"></div></details></div>';
  document.querySelector("#contact-period").addEventListener("change", event =>
    navigateToUrl(contactUrl(detail.id, event.target.value)));
  document.querySelector("#contact-history").addEventListener("toggle", event => {
    if (event.target.open && detail.history === null && activeContactDetail(detail, generation))
      void loadContactHistory(detail, generation);
  });
  applyViewState();
  await loadContactOperations(detail, generation);
}

function counterpartyNameCell(row) {
  return '<a class="counterparty-link" data-spa href="' + escapeHtml(contactUrl(row.counterparty_id)) +
    '"><strong>' + escapeHtml(row.display_name) + '</strong></a>';
}

function validCounterpartyName(value) {
  return typeof value === "string" && value.trim().length > 0 &&
    !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(value);
}

function updateCounterpartyNameControls() {
  const edit = counterpartyNameEditor;
  const busy = Boolean(edit?.busy);
  document.querySelector("#counterparty-name-form").setAttribute("aria-busy", String(busy));
  document.querySelector("#counterparty-name-input").disabled = busy;
  document.querySelector("#save-counterparty-name").disabled = busy || Boolean(edit?.conflict);
  document.querySelector("#save-counterparty-name").textContent = t(busy ? "contacts.savingName" : "contacts.saveName");
  document.querySelector("#cancel-counterparty-name").disabled = busy;
  document.querySelector("#close-counterparty-name").disabled = busy;
}

function openCounterpartyNameEditor(id, trigger, row = counterpartyRowsById.get(id)) {
  if (!row) return;
  counterpartyNameEditor = {
    row: {...row}, trigger, busy: false, conflict: null, historyRequest: 0,
    pageUrl: window.location.pathname + (window.location.search || ""),
    pageState: window.history?.state || null,
  };
  document.querySelector("#counterparty-name-input").value = row.display_name;
  document.querySelector("#counterparty-name-input").removeAttribute("aria-invalid");
  document.querySelector("#counterparty-name-error").textContent = "";
  document.querySelector("#counterparty-name-conflict").hidden = true;
  document.querySelector("#counterparty-name-history").open = false;
  updateCounterpartyNameControls();
  document.querySelector("#counterparty-name-dialog").showModal();
  document.querySelector("#counterparty-name-input").focus();
  void loadCounterpartyNameHistory();
}

function closeCounterpartyNameEditor(force = false) {
  const edit = counterpartyNameEditor;
  if (!edit) return true;
  if (!force && edit.busy) return false;
  if (!force && document.querySelector("#counterparty-name-input").value !== edit.row.display_name &&
      !window.confirm(t("contacts.discardName"))) return false;
  counterpartyNameEditor = null;
  document.querySelector("#counterparty-name-dialog").close();
  if (edit.trigger?.isConnected) edit.trigger.focus();
  return true;
}

async function loadCounterpartyNameHistory() {
  const edit = counterpartyNameEditor;
  if (!edit) return;
  const request = ++edit.historyRequest;
  const body = document.querySelector("#counterparty-name-history-body");
  const retry = document.querySelector("#retry-counterparty-name-history");
  body.textContent = t("common.loading");
  retry.hidden = true;
  try {
    const result = await fetchJSON("/api/counterparties/" + encodeURIComponent(edit.row.counterparty_id) + "/name-history");
    if (counterpartyNameEditor !== edit || request !== edit.historyRequest) return;
    body.innerHTML = result.changes.length ? '<ol class="counterparty-name-history-list">' +
      result.changes.map((change) => {
        const actor = change.actor || t(change.change_source === "sheet" ? "contacts.sheetActor" : "contacts.localActor");
        const time = new Intl.DateTimeFormat(intlLocale(), {dateStyle: "medium", timeStyle: "short"}).format(new Date(change.changed_at));
        return '<li><div>' + escapeHtml(change.old_name) + ' → <strong>' + escapeHtml(change.new_name) +
          '</strong></div><small>' + escapeHtml(time) + ' · ' + escapeHtml(actor) + '</small></li>';
      }).join("") + '</ol>' : '<p>' + escapeHtml(t("contacts.noNameHistory")) + '</p>';
  } catch (_) {
    if (counterpartyNameEditor !== edit || request !== edit.historyRequest) return;
    body.textContent = t("contacts.historyError");
    retry.hidden = false;
  }
}

function acceptCurrentCounterpartyName() {
  const edit = counterpartyNameEditor;
  if (!edit?.conflict || edit.busy) return;
  edit.row = {...edit.row, ...edit.conflict};
  edit.conflict = null;
  document.querySelector("#counterparty-name-conflict").hidden = true;
  document.querySelector("#counterparty-name-error").textContent = "";
  updateCounterpartyNameControls();
  document.querySelector("#counterparty-name-input").focus();
  void loadCounterpartyNameHistory();
}

async function submitCounterpartyName(event) {
  event.preventDefault();
  const edit = counterpartyNameEditor;
  if (!edit || edit.busy || edit.conflict) return;
  const input = document.querySelector("#counterparty-name-input");
  const errorLabel = document.querySelector("#counterparty-name-error");
  errorLabel.textContent = "";
  input.removeAttribute("aria-invalid");
  if (!validCounterpartyName(input.value)) {
    errorLabel.textContent = t("contacts.invalidName");
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  edit.busy = true;
  updateCounterpartyNameControls();
  let result;
  try {
    result = await fetchJSON("/api/counterparties/" + encodeURIComponent(edit.row.counterparty_id) + "/rename", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({display_name: input.value, expected_row_version: edit.row.row_version}),
    });
  } catch (error) {
    if (counterpartyNameEditor !== edit) return;
    if (error.status === 409 && error.code === "stale_counterparty" && error.current) {
      edit.conflict = error.current;
      document.querySelector("#counterparty-current-name").textContent = t("contacts.currentName", {name: error.current.display_name});
      document.querySelector("#counterparty-name-conflict").hidden = false;
      errorLabel.textContent = t("contacts.nameConflict");
    } else {
      errorLabel.textContent = error.code === "invalid_name" ? t("contacts.invalidName")
        : error.code === "counterparty_busy" ? t("contacts.nameBusy")
        : error.status === 404 ? t("contacts.nameMissing") : error.message;
    }
  } finally {
    edit.busy = false;
    if (counterpartyNameEditor === edit) updateCounterpartyNameControls();
  }
  if (!result || counterpartyNameEditor !== edit) return;
  counterpartyRowsById.set(edit.row.counterparty_id, {...edit.row, ...result});
  closeCounterpartyNameEditor(true);
  showToast(t(result.changed ? "contacts.nameSaved" : "contacts.nameUnchanged"));
  if (state.view === "contacts") {
    rememberContactsListPosition();
    try {
      await renderContacts();
      Array.from(document.querySelectorAll("[data-counterparty-menu]"))
        .find((button) => button.dataset.counterpartyMenu === edit.row.counterparty_id)?.focus({preventScroll: true});
    } catch (_) {
      showToast(t("contacts.nameRefreshError"), true);
    }
  } else if (state.view === "contact-detail" && state.contactDetail.id === edit.row.counterparty_id) {
    const current = state.contactDetail;
    current.data.counterparty = {...current.data.counterparty, ...result};
    current.data.counterparty.ui_context = {...current.data.counterparty.ui_context, title: result.display_name};
    document.querySelector("#contact-identity").innerHTML = contactIdentityMarkup(current.data.counterparty);
    applyViewState();
    current.history = null;
    ++current.historyRequest;
    if (document.querySelector("#contact-history").open) void loadContactHistory(current, currentRenderGeneration);
    document.querySelector("#contact-identity [data-counterparty-menu]")?.focus({preventScroll: true});
  }
}

async function refreshDashboard() {
  if (state.view === "contact-detail" || state.view === "expense-detail" || (state.view === "review" && state.review.selectedReviewId && !state.detailPeriodResolved)) {
    await renderCurrentView();
    return;
  }
  refreshButton.disabled = true;
  refreshButton.classList.add("busy");
  const period = state.period;
  const generation = currentRenderGeneration;
  try {
    await requestDashboardRefresh({period, showSuccessToast: false});
    if (state.posting.staleRefresh?.period === period) state.posting.staleRefresh = null;
    if (generation !== currentRenderGeneration) return;
    showToast(t("refresh.done", {period}));
    await renderCurrentView();
  } catch (error) {
    if (generation === currentRenderGeneration) showToast(error.message, true);
  } finally {
    refreshButton.classList.remove("busy");
    applyViewState();
  }
}

async function requestDashboardRefresh({showSuccessToast = true, period = state.period} = {}) {
  const response = await fetchJSON("/api/dashboard/refresh", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({period, as_of: new Date().toISOString().slice(0, 10)}),
  });
  if (showSuccessToast) showToast(t("refresh.done", {period}));
  return response;
}

async function retryPostingRefresh() {
  const period = state.posting.staleRefresh?.period;
  if (!period || state.view !== "review" || period !== state.period) return;
  const generation = currentRenderGeneration;
  try {
    await requestDashboardRefresh({period, showSuccessToast: false});
    if (state.posting.staleRefresh?.period === period) state.posting.staleRefresh = null;
    if (generation === currentRenderGeneration) await renderCurrentView();
  } catch (error) {
    state.posting.staleRefresh = {period, error: error.message};
    if (generation !== currentRenderGeneration) return;
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
    rowStatus: String(raw.outcome || raw.status || raw.state || detail.outcome || detail.status || raw.preview_bucket || "unknown").trim(),
    previewBucket: raw.preview_bucket || detail.preview_bucket || null,
    postingDeferredUntil: raw.posting_deferred_until || detail.posting_deferred_until || null,
    structuredReasons: [...(raw.blockers || []), ...(detail.blockers || []), ...(raw.blocking_issues || [])].filter(r => r && typeof r === "object"),
    documentId: raw.document_id || detail.document_id || null,
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
          <thead><tr><th>${escapeHtml(t("transactions.date"))}</th><th>${escapeHtml(t("transactions.counterpartyDocument"))}</th><th>${escapeHtml(t("transactions.status"))} ${AccountingHelp.term("posting")}</th><th>${escapeHtml(t("transactions.amount"))}</th><th>${escapeHtml(t("review.postingResultMessage"))}</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${formatDate(row.transactionDate)}</td>
                <td class="cell-primary"><strong>${escapeHtml(row.description || row.reviewId || row.transactionId || t("common.noId"))}</strong><small>${escapeHtml(row.entryType || "—")}</small></td>
                <td>${AccountingHelp.cell(postingHelpContext(row))}</td>
                <td class="amount">${row.amountEur ? eur(row.amountEur) : "—"}</td>
                <td class="posting-result-message">${row.structuredReasons.length ? row.structuredReasons.map(reason => escapeHtml(AccountingHelp.reasonInfo(reason)[0][state.locale === "en" ? 1 : 0])).join(" · ") : escapeHtml(t("issues.default"))}</td>
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
  const submissionPeriod = state.period;
  const generation = currentRenderGeneration;
  const isActive = () => generation === currentRenderGeneration && state.view === "review" && state.period === submissionPeriod;
  const pendingItems = state.posting.pendingItems.length ? state.posting.pendingItems : preview.ready;
  state.posting.isSubmitting = true;
  renderPostingConfirmDialog();
  try {
    const payload = await fetchJSON("/api/review/post-ready", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        period: submissionPeriod,
        items: buildPostReadyItems(pendingItems),
      }),
    });
    state.posting.lastResult = normalizePostingResult(payload, pendingItems);
    state.posting.lastResultPeriod = submissionPeriod;
    if (!isActive()) {
      if (state.posting.lastResult.summary.postedCount > 0) state.posting.staleRefresh = {period: submissionPeriod, error: ""};
      return;
    }
    closePostingConfirmDialog();
    if (state.posting.lastResult.summary.postedCount > 0) {
      try {
        await requestDashboardRefresh({showSuccessToast: false, period: submissionPeriod});
        state.posting.staleRefresh = null;
      } catch (error) {
        state.posting.staleRefresh = {period: submissionPeriod, error: error.message};
      }
    }
    if (!isActive()) return;
    state.posting.isSubmitting = false;
    showToast(postingResultToast(state.posting.lastResult));
    await renderCurrentView();
  } catch (error) {
    if (!isActive()) return;
    postingConfirmStatus.textContent = error.message;
    showToast(error.message, true);
  } finally {
    state.posting.isSubmitting = false;
    if (isActive() && postingConfirmDialog.open) renderPostingConfirmDialog();
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
  state.googleFolder = null;
  renderGoogleFolderSelection();
  setIntakeKind(kind);
  setIntakeSource("upload");
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
  formElements.issued_on.max = localDateKey();
  if (!prefill && applyIntakeDraft(formElements)) setIntakeNotice([t("intake.draftRestored")]);
  updateIntakeConsistencyHint();
  updateFilePrompt();
  void refreshGooglePickerAvailability();
  if (!dialog.open) dialog.showModal();
}

function closeIntake() {
  if (dialog.open) dialog.close();
  state.googleFolder = null;
  renderGoogleFolderSelection();
}

const INTAKE_DRAFT_FIELDS = ["issued_on", "counterparty_name", "document_number", "currency", "gross", "taxable_base", "vat", "drive_url"];

function readIntakeDraftValues(formElements) {
  const values = {};
  INTAKE_DRAFT_FIELDS.forEach((name) => {
    const element = formElements[name];
    if (element && typeof element.value === "string" && element.value !== "") values[name] = element.value;
  });
  return values;
}

function intakeDraftIsEmpty(values) {
  const names = Object.keys(values);
  return names.length === 0 || (names.length === 1 && values.currency === "EUR");
}

function persistIntakeDraft() {
  try {
    const values = readIntakeDraftValues(intakeForm.elements);
    if (intakeDraftIsEmpty(values)) {
      localStorage.removeItem(INTAKE_DRAFT_STORAGE_KEY);
      return;
    }
    localStorage.setItem(INTAKE_DRAFT_STORAGE_KEY, JSON.stringify({schema: 1, values}));
  } catch {
    // Draft persistence is a convenience; the form keeps working without it.
  }
}

function loadIntakeDraft() {
  try {
    const parsed = JSON.parse(localStorage.getItem(INTAKE_DRAFT_STORAGE_KEY) || "null");
    if (!parsed || parsed.schema !== 1 || typeof parsed.values !== "object" || parsed.values === null) return null;
    return parsed.values;
  } catch {
    return null;
  }
}

function clearIntakeDraft() {
  try {
    localStorage.removeItem(INTAKE_DRAFT_STORAGE_KEY);
  } catch {
    // A store that refuses deletes only means the draft survives longer.
  }
}

function applyIntakeDraft(formElements) {
  const values = loadIntakeDraft();
  if (!values || intakeDraftIsEmpty(values)) return false;
  let applied = false;
  INTAKE_DRAFT_FIELDS.forEach((name) => {
    const element = formElements[name];
    if (!element || typeof values[name] !== "string") return;
    element.value = values[name];
    applied = true;
  });
  return applied;
}

function setIntakeKind(kind) {
  state.intakeKind = kind;
  intakeKind.value = kind;
  document.querySelectorAll(".segmented-control button").forEach((button) => {
    button.classList.toggle("active", button.dataset.kind === kind);
  });
  document.querySelector("#counterparty-label").textContent =
    kind === "income_invoice" ? t("fields.client") : t("fields.supplier");
  if (state.intakeSource === "upload" && !intakeFile.files[0]) updateFilePrompt();
  document.querySelectorAll(".expense-only").forEach((element) => {
    element.hidden = kind === "income_invoice";
  });
}

function setIntakeSource(source) {
  state.intakeSource = source === "google_drive" ? "google_drive" : "upload";
  const usingGoogleDrive = state.intakeSource === "google_drive";
  intakeFile.required = !usingGoogleDrive;
  intakeFile.disabled = usingGoogleDrive;
  fileDrop.hidden = usingGoogleDrive;
  googleDriveUrlField.hidden = !usingGoogleDrive;
  googleDriveUrl.required = usingGoogleDrive;
  googleDriveUrl.disabled = !usingGoogleDrive;
  googlePickerHint.hidden = !usingGoogleDrive;
  googleFolderControls.hidden = usingGoogleDrive || !state.googlePicker?.enabled;
  document.querySelectorAll("[data-intake-source]").forEach((button) => {
    const active = button.dataset.intakeSource === state.intakeSource;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (usingGoogleDrive) {
    googleDriveUrl.focus();
  } else if (!intakeFile.files[0]) {
    updateFilePrompt();
  }
}

function renderGoogleFolderSelection() {
  if (!googleFolderSelection) return;
  googleFolderSelection.textContent = state.googleFolder
    ? t("intake.googleFolderSelected", {name: state.googleFolder.name})
    : "";
}

async function refreshGooglePickerAvailability() {
  try {
    const config = await fetchJSON("/api/google-picker/config");
    state.googlePicker = {enabled: Boolean(config?.enabled)};
  } catch (_) {
    state.googlePicker = {enabled: false};
  }
  googleFolderControls.hidden = state.intakeSource !== "upload" || !state.googlePicker.enabled;
}

function loadGooglePickerApi() {
  if (window.google?.picker) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-google-picker-api]');
    if (existing) {
      existing.addEventListener("load", () => window.gapi.load("picker", resolve), {once: true});
      existing.addEventListener("error", reject, {once: true});
      return;
    }
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.async = true;
    script.dataset.googlePickerApi = "true";
    script.addEventListener("load", () => window.gapi.load("picker", resolve), {once: true});
    script.addEventListener("error", () => reject(new Error("Google Picker failed to load")), {once: true});
    document.head.append(script);
  });
}

async function openGoogleFolderPicker() {
  chooseGoogleFolder.disabled = true;
  try {
    const config = await fetchJSON("/api/google-picker/config");
    if (!config?.enabled || !config.developer_key || !config.app_id || !config.access_token) {
      throw new Error(t("intake.googlePickerUnavailable"));
    }
    await loadGooglePickerApi();
    const view = new window.google.picker.DocsView(window.google.picker.ViewId.FOLDERS)
      .setSelectFolderEnabled(true)
      .setMode(window.google.picker.DocsViewMode.LIST);
    const picker = new window.google.picker.PickerBuilder()
      .addView(view)
      .setOAuthToken(config.access_token)
      .setDeveloperKey(config.developer_key)
      .setAppId(config.app_id)
      .setOrigin(window.location.origin)
      .setCallback((data) => {
        if (data.action !== window.google.picker.Action.PICKED) return;
        const selected = data[window.google.picker.Response.DOCUMENTS]?.[0];
        const id = selected?.[window.google.picker.Document.ID];
        const name = selected?.[window.google.picker.Document.NAME];
        if (!/^[A-Za-z0-9_-]{10,256}$/.test(String(id || ""))) return;
        state.googleFolder = {id: String(id), name: String(name || id)};
        renderGoogleFolderSelection();
      })
      .build();
    picker.setVisible(true);
  } catch (error) {
    intakeStatus.textContent = error.message || t("intake.googlePickerUnavailable");
  } finally {
    chooseGoogleFolder.disabled = false;
  }
}

function isGoogleDriveUrl(value) {
  try {
    const url = new URL(String(value || ""));
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    const parts = url.pathname.split("/").filter(Boolean);
    if (url.hostname === "drive.google.com") {
      return (parts[0] === "file" && parts[1] === "d" && /^[A-Za-z0-9_-]{10,256}$/.test(parts[2] || ""))
        || (["open", "uc"].includes(parts[0]) && /^[A-Za-z0-9_-]{10,256}$/.test(url.searchParams.get("id") || ""));
    }
    return url.hostname === "docs.google.com"
      && ["document", "spreadsheets", "presentation", "drawings"].includes(parts[0])
      && parts[1] === "d"
      && /^[A-Za-z0-9_-]{10,256}$/.test(parts[2] || "");
  } catch (_) {
    return false;
  }
}

function updateFilePrompt() {
  fileLabel.textContent = state.intakeKind === "income_invoice"
    ? t("intake.incomeFile")
    : t("intake.expenseFile");
}

function updateIntakeConsistencyHint() {
  const hint = document.querySelector("#intake-consistency-hint");
  if (!hint) return;
  const formElements = intakeForm.elements;
  const gross = Number.parseFloat(formElements.gross?.value || "");
  const base = Number.parseFloat(formElements.taxable_base?.value || "");
  const vat = Number.parseFloat(formElements.vat?.value || "");
  const mismatch = state.intakeKind === "expense_invoice"
    && Number.isFinite(gross) && Number.isFinite(base) && Number.isFinite(vat)
    && Math.abs(gross - (base + vat)) > 0.01;
  hint.hidden = !mismatch;
  hint.textContent = mismatch
    ? t("intake.consistencyHint", {expected: (base + vat).toFixed(2), total: gross.toFixed(2)})
    : "";
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
  const expenseDetail = state.view === "expense-detail";
  const contactDetail = state.view === "contact-detail";
  const detail = contactDetail || expenseDetail || (state.view === "review" && Boolean(state.review.selectedReviewId));
  const navView = contactDetail ? "contacts" : expenseDetail ? "expenses" : state.view;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === navView);
    button.setAttribute("href", buildRouteUrl(button.dataset.view));
  });
  pageTitle.textContent = contactDetail ? t("contacts.cardTitle") : expenseDetail ? t("expense.title") : t(`titles.${state.view}`);
  if (periodSelect) {
    const control = periodSelect.closest?.(".period-control");
    if (control) control.hidden = contactDetail || state.view === "settings";
    periodSelect.disabled = Boolean(detail);
    periodSelect.title = detail ? t("toolbar.periodLocked") : "";
    periodSelect.value = detail && !state.detailPeriodResolved ? "" : state.period;
  }
  if (newEntryButton) newEntryButton.hidden = Boolean(detail) || state.view === "settings";
  if (refreshButton) {
    refreshButton.hidden = state.view === "settings";
    const label = t(contactDetail ? "contacts.refresh" : expenseDetail ? "expense.refresh" : "toolbar.refresh");
    refreshButton.title = label;
    refreshButton.setAttribute("aria-label", label);
    refreshButton.disabled = Boolean(detail && !contactDetail && !state.detailPeriodResolved);
  }
}

if (typeof globalThis !== "undefined") {
  globalThis.__AUTONOMO_WEB_UI_TEST_HOOKS__ = {
    createExpensePager,
    replaceExpenseResults,
    captureDashboardChartState,
    restoreDashboardChartState,
    refreshDashboardExpenses,
    expenseLocaleKeys: () => ({ru: Object.keys(messages.ru).filter(key => key.startsWith("expense.")), en: Object.keys(messages.en).filter(key => key.startsWith("expense."))}),
    renderExpensePreview: (payload, locale = "ru", sourceUrl = null) => {
      const previousLocale = state.locale;
      state.locale = locale;
      AccountingHelp.setLocale(locale);
      try { return expenseSections(payload, sourceUrl); } finally { state.locale = previousLocale; AccountingHelp.setLocale(previousLocale); }
    },
    renderExpenseMetric: (kind, summary, locale = "ru") => {
      const previousLocale = state.locale;
      state.locale = locale;
      try { return expenseMetric(kind, summary); } finally { state.locale = previousLocale; }
    },
    renderRecentPreview: (rows, locale = "ru") => {
      const previousLocale = state.locale;
      state.locale = locale;
      AccountingHelp.setLocale(locale);
      try { return transactionTable(rows); } finally { state.locale = previousLocale; AccountingHelp.setLocale(previousLocale); }
    },
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
    parseRoute,
    routePathFor,
    buildRouteUrl,
    autoResolveCoveredIssues,
    mapConfirmErrorToQuestion,
    buildConfirmFxSpec,
    questionAnswerMap,
    fxChoiceNeeded,
    isGoogleDriveUrl,
  };
}

if (hasDOM) {
  const refreshVisibleExpenseData = () => {
    if (document.visibilityState !== "visible" || dialog?.open || postingConfirmDialog?.open || chartDialog?.open || document.querySelector("#status-help-dialog")?.open) return;
    if (state.view === "expenses") refreshExpenseView?.();
    if (state.view === "dashboard") void refreshDashboardExpenses();
  };
  const handleChartResize = debounce(() => {
    if (chartDialog?.open) renderChartDialogFigure();
    for (const [slotId, entry] of [...viewChartRegistry]) {
      const slot = document.querySelector(`#${slotId}`);
      if (!slot) {
        viewChartRegistry.delete(slotId);
        continue;
      }
      const width = chartHostWidth(slot);
      if (!width || width === entry.spec.width) continue;
      entry.spec.width = width;
      entry.render(slot, entry.spec);
    }
  }, 200);
  window.addEventListener("resize", handleChartResize);
  chartDialogClose?.addEventListener("click", () => closeChartDialog());
  chartDialog?.addEventListener("close", () => {
    const opener = chartDialogOpener;
    chartDialogEntry = null;
    chartDialogOpener = null;
    if (opener?.isConnected) opener.focus({preventScroll: true});
  });
  document.addEventListener("visibilitychange", refreshVisibleExpenseData);
  document.addEventListener("visibilitychange", handleChartResize);
  window.setInterval(refreshVisibleExpenseData, 60000);
  localeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      void changeLocale(button.dataset.locale);
    });
  });

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    closeCounterpartyMenuFromOutside(event.target);
    if (event.target.closest("#counterparty-menu-rename")) {
      const selected = counterpartyMenu;
      closeCounterpartyMenu(false);
      if (selected) openCounterpartyNameEditor(selected.row.counterparty_id, selected.trigger, selected.row);
      return;
    }
    if (event.target.closest("[data-reload-view]")) { window.location.reload(); return; }
    if (event.target.closest("[data-retry-view]")) { void renderCurrentView(); return; }
    if (event.target.closest("[data-refresh-calculation]")) { void refreshDashboard(); return; }
    handleSpaClick(event);
  });

  window.addEventListener("popstate", () => {
    if (AccountingHelp.handlePopState()) return;
    applyRouteFromLocation();
  });
  document.addEventListener("keydown", event => {
    if (!counterpartyMenu) return;
    if (event.key === "Escape") { event.preventDefault(); closeCounterpartyMenu(); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      document.querySelector("#counterparty-menu-rename").focus();
    } else if (event.key === "Tab") closeCounterpartyMenu();
  });
  document.addEventListener("scroll", () => closeCounterpartyMenu(false), true);
  window.addEventListener("resize", () => closeCounterpartyMenu(false));
  window.addEventListener("beforeunload", event => {
    if (settingsController && (settingsController.isDirty() || settingsController.isBusy())) { event.preventDefault(); event.returnValue = ""; }
    const edit = counterpartyNameEditor;
    if (edit && (edit.busy || document.querySelector("#counterparty-name-input").value !== edit.row.display_name)) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  toastClose?.addEventListener("click", hideToast);

  periodSelect.addEventListener("change", () => {
    state.period = periodSelect.value;
    navigateToRoute(state.view, {replace: true});
  });

  newEntryButton.addEventListener("click", () => {
    const kind = state.view === "income" ? "income_invoice" : "expense_invoice";
    openIntake(kind);
  });

  refreshButton.addEventListener("click", refreshDashboard);
  document.querySelector("#counterparty-name-form").addEventListener("submit", submitCounterpartyName);
  document.querySelector("#cancel-counterparty-name").addEventListener("click", () => closeCounterpartyNameEditor());
  document.querySelector("#close-counterparty-name").addEventListener("click", () => closeCounterpartyNameEditor());
  document.querySelector("#counterparty-name-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeCounterpartyNameEditor();
  });
  document.querySelector("#accept-counterparty-current-name").addEventListener("click", acceptCurrentCounterpartyName);
  document.querySelector("#retry-counterparty-name-history").addEventListener("click", () => void loadCounterpartyNameHistory());
  document.querySelector("#close-dialog").addEventListener("click", closeIntake);
  document.querySelector("#cancel-dialog").addEventListener("click", closeIntake);
  document.querySelector("#close-posting-dialog").addEventListener("click", closePostingConfirmDialog);
  document.querySelector("#cancel-posting-dialog").addEventListener("click", closePostingConfirmDialog);
  chooseGoogleFolder.addEventListener("click", () => {
    void openGoogleFolderPicker();
  });
  confirmPostingButton.addEventListener("click", () => {
    void submitPostingReady();
  });

  document.querySelectorAll(".segmented-control button").forEach((button) => {
    if (button.dataset.kind) {
      button.addEventListener("click", () => setIntakeKind(button.dataset.kind));
    }
  });

  document.querySelectorAll("[data-intake-source]").forEach((button) => {
    button.addEventListener("click", () => setIntakeSource(button.dataset.intakeSource));
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
    const menuButton = event.target.closest("[data-counterparty-menu]");
    if (menuButton) {
      event.preventDefault();
      toggleCounterpartyMenu(menuButton);
      return;
    }
    if (event.target.closest("#contact-load-more")) {
      void loadContactOperations(state.contactDetail, currentRenderGeneration); return;
    }
    if (event.target.closest("#contact-retry-history")) {
      void loadContactHistory(state.contactDetail, currentRenderGeneration); return;
    }
    const contactRow = event.target.closest("[data-counterparty-row]");
    if (contactRow && counterpartyRowClickAllowed(event)) {
      contactRow.querySelector(".counterparty-link")?.focus({preventScroll: true});
      navigateToUrl(contactUrl(contactRow.dataset.counterpartyRow)); return;
    }
    const viewButton = event.target.closest("[data-nav-view]");
    if (viewButton) {
      navigateToRoute(viewButton.dataset.navView, viewButton.dataset.navTab ? {tab: viewButton.dataset.navTab} : {});
      return;
    }
    const reviewTabButton = event.target.closest("[data-review-tab]");
    if (reviewTabButton) {
      navigateToRoute("review", {tab: reviewTabButton.dataset.reviewTab});
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
    if (state.intakeSource === "upload" && !intakeFile.files[0]) {
      intakeStatus.textContent = t("intake.selectFile");
      return;
    }
    if (state.intakeSource === "google_drive" && !isGoogleDriveUrl(googleDriveUrl.value)) {
      intakeStatus.textContent = t("intake.googleDriveInvalid");
      googleDriveUrl.focus();
      return;
    }
    submitIntake.disabled = true;
    intakeStatus.textContent = t("intake.processing");
    try {
      const request = state.intakeSource === "google_drive"
        ? {
          url: "/api/intake/google-drive",
          options: {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify((() => {
              const fields = Object.fromEntries(new FormData(intakeForm).entries());
              delete fields.drive_url;
              if (fields.kind === "expense_invoice") fields.defer_counterparty = "1";
              return {fields, drive_url: googleDriveUrl.value.trim()};
            })()),
          },
        }
        : {
          url: "/api/intake",
          options: {
            method: "POST",
            body: (() => {
              const formData = new FormData(intakeForm);
              if (formData.get("kind") === "expense_invoice") formData.set("defer_counterparty", "1");
              if (state.googleFolder?.id) formData.set("google_folder_id", state.googleFolder.id);
              return formData;
            })(),
          },
        };
      const result = await fetchJSON(request.url, {
        ...request.options,
        fallbackMessage: t("intake.failed"),
      });
      intakeStatus.textContent = t("intake.accepted", {
        id: shortId(result.system_marker),
      });
      clearIntakeDraft();
      showToast(t("intake.acceptedToast", {period: result.period}));
      setTimeout(() => {
        closeIntake();
        if (result.kind === "expense_invoice" && result.transaction_id) {
          navigateToRoute("review", {reviewId: "transaction:" + result.transaction_id, period: result.period});
        } else {
          renderCurrentView();
        }
      }, 700);
    } catch (error) {
      intakeStatus.textContent = error.message;
    } finally {
      submitIntake.disabled = false;
    }
  });

  intakeForm.addEventListener("input", debounce(() => {
    persistIntakeDraft();
    updateIntakeConsistencyHint();
  }, 250));

  applyStaticTranslations();
  init();
}

function postingHelpContext(row) {
  return {domain: "transaction", title: row.description, state: row.previewBucket || row.rowStatus || "unknown",
    posting: {preview_bucket: row.previewBucket, posting_deferred_until: row.postingDeferredUntil, blockers: row.structuredReasons || []},
    reasons: [], actions: row.transactionId ? [{kind: "review", transaction_id: row.transactionId}] : [],
    facts: {transaction_date: row.transactionDate}};
}

function calculationHelp(form) {
  if (form.display_state !== "unavailable") return "";
  return `<p>${escapeHtml(AccountingHelp.word("refreshHint"))}</p><button type="button" class="secondary-button" data-refresh-calculation>${escapeHtml(AccountingHelp.word("refresh"))}</button>`;
}
