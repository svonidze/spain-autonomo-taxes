# Веб-мастер настройки хранилищ и резервных копий

## Кратко

Добавить в существующий интерфейс раздел «Хранилища», который полностью
подключает Google Drive и Yandex S3 по уже выпущенным credentials, выполняет
проверки и reconcile, показывает backup health и позволяет вручную назначить
primary.

Облачные проекты, bucket, service account, lifecycle и Google OAuth client
создаются вне приложения. Все пользователи из `allowed_tailscale_logins`
получают доступ к мастеру, как выбрано пользователем.

## Модель и backend

- Выпустить additive schema migration 19:
  - добавить в `storage_backends` уникальный флаг `is_primary_target`;
  - добавить `storage_operations` для фоновых операций, прогресса, безопасных
    ошибок и аудита principal;
  - при миграции определить существующий Google backend как primary target
    только если он уже покрывает все source-файлы primary-репликами.
- Убрать зависимость от имени `google_primary_rw`: reconcile назначает новую
  реплику primary только для backend с `is_primary_target=1`.
- Реализовать транзакционную ручную promotion:
  - при наличии файлов требуется 100% доступных реплик и свежая полная
    SHA-256 проверка;
  - затем одной транзакцией переключаются backend target и все
    `file_replicas.is_primary`;
  - при пустом каталоге достаточно успешного connection test.
- Вынести настройку, тестирование, reconcile и promotion в общий
  `StorageAdminService`, используемый CLI и web.
- Расширить чтение документов: если локальная копия недоступна, скачать
  primary, затем mirror по `read_priority`, проверить SHA-256, положить во
  временный cache и только потом отдать пользователю.

## Existing Google Archive Adoption

- Существующий Drive-архив обслуживается отдельным read-only backend
  `google_archive_ro` с service-account credential и canonical
  `provider_locator=Google fileId`.
- Перед любым upload сначала искать existing original, проверять его
  `files.content_sha256` и регистрировать URL, понятное имя и archive path как
  metadata. Не создавать `sha256-*` копию, если bytes уже есть в Drive.
- Для текущей миграции принять все 11 original files, вручную promote их и
  явно retire legacy managed backend. Только после новой backup/restore drill
  перенести managed folder в Google Drive Trash.
- Intake принимает Google Drive URL либо local upload. URL становится
  оригинальной Drive replica после download/hash/extraction; local upload
  создаёт Drive copy только в folder, который пользователь выбрал через
  Google Picker. До выбора default folder local file остаётся local primary +
  Yandex mirror.

## Web API и безопасность

- Добавить API:
  - `GET /api/storage` — summary, backends, coverage, backup health,
    operations;
  - `POST /api/storage/google/start` и callback
    `/api/storage/oauth/google/callback`;
  - `POST /api/storage/yandex/configure`;
  - `POST /api/storage/backends/{key}/test`;
  - `POST /api/storage/backends/{key}/reconcile`;
  - `POST /api/storage/backends/{key}/promote`;
  - `GET /api/storage/operations/{id}`.
- Все storage POST требуют session, обязательный same-origin `Origin`, JSON
  content type и ограничение размера запроса.
- Секреты никогда не сохраняются в SQLite, не возвращаются в GET, не попадают
  в operation errors и логи.
- Ввести обязательный абсолютный `AUTONOMO_CREDENTIALS_ROOT` для Tailscale
  production:
  - каталог `0700`, файлы `0600`;
  - запрет symlink;
  - запись через временный файл, проверку и atomic replace.
- Google wizard:
  - принимает OAuth JSON только типа Web application;
  - показывает точный HTTPS callback URL, который нужно заранее разрешить в
    Google Cloud;
  - использует PKCE, одноразовый state, TTL 10 минут и привязку к Tailscale
    principal;
  - получает доступ к existing file/folder через Google Picker или проверяет
    доступ к указанному folder ID; не создаёт отдельную папку для уже
    существующего архива;
  - сохраняет refresh token только после успешного Drive API smoke.
- Yandex wizard:
  - принимает bucket, endpoint, prefixes, Access Key ID и Secret Key;
  - генерирует независимые crypt-настройки для `data/evidence`,
    `backups/daily`, `backups/monthly`;
  - проверяет put/get/head/list и обязательный delete-denied;
  - не принимает cloud-admin credentials и не меняет bucket lifecycle.
- После успешной настройки создать одноразовый recovery-export:
  - содержит rclone crypt-конфигурацию, S3 recovery-материалы и Google OAuth
    client config, но не Google refresh token;
  - шифруется в браузере AES-256-GCM, ключ выводится из passphrase через
    PBKDF2-SHA256;
  - passphrase не отправляется серверу;
  - plaintext bundle доступен только по одноразовому токену и очищается из
    памяти после скачивания.
- В v1 не добавлять удаление backend или просмотр сохранённых секретов.
  Разрешены повторная настройка и rotation с обязательной новой проверкой.

## Интерфейс и backups

- Первым implementation-шагом создать repo-local `DESIGN.md`, зафиксировав
  существующий визуальный язык, RU/EN, accessibility и Storage flow.
- Добавить пункт «Хранилища» в отдельную административную группу sidebar; для
  этого view скрывать period selector и кнопку добавления документа.
- Страница содержит:
  - общий health: files, attachments, primary coverage, mirror coverage, last
    daily/monthly backup;
  - карточки Google Drive, Yandex S3 и local staging;
  - статусы `Connected`, `Needs setup`, `Reconciling`, `Degraded`,
    `Credential expired`;
  - действия Configure/Rotate, Test, Reconcile и Promote.
- Мастер: Provider → Credentials → Target/prefixes → Validation → Save →
  Recovery export.
- Promotion всегда отдельное подтверждаемое действие после reconcile.
- Фоновые операции возвращают `202 + operation_id`; UI опрашивает progress.
  При перезапуске незавершённые операции помечаются `interrupted` и безопасно
  повторяются.
- `ops/backup.sh` атомарно пишет обезличенные daily/monthly status-файлы в
  cache.
- Systemd units загружают дополнительный optional `storage-runtime.env`,
  которым управляет wizard; основной `runtime.env` веб не редактирует.
- Health thresholds:
  - daily stale после 36 часов;
  - monthly stale после 35 дней;
  - coverage меньше 100% — degraded;
  - отсутствие доступного primary — critical.
- Lifecycle отображать как `Managed externally / not verifiable with uploader
  credentials`.

## Проверки и rollout

- Schema/API tests: migration 18→19, primary inference, unique target, atomic
  promotion, interrupted operations.
- Security tests: principal/session/origin enforcement, OAuth state
  replay/expiry, wrong scope, symlink rejection, `0600`, отсутствие секретов в
  JSON/log/errors.
- Provider tests: Google OAuth callback and folder creation, Yandex rclone
  generation, delete-denied, credential rotation rollback.
- Behavior tests: incomplete reconcile blocks promotion; successful promotion
  affects future intake; remote primary failure falls back to verified mirror.
- UI tests: RU/EN strings, wizard states, progress polling, recovery encryption,
  отсутствие credentials в `localStorage`.
- Backup tests: generated `storage-runtime.env`, daily/monthly status, failure
  status and existing `OnFailure`.
- Rollout:
  1. задеплоить schema 19 без изменения существующих credential files;
  2. показать текущие Google/Yandex backends как already connected;
  3. проверить status/reconcile через UI;
  4. вручную подтвердить Google promotion;
  5. выполнить daily backup и isolated restore drill;
  6. сохранить CLI как аварийный и automation-интерфейс.

## Зафиксированные допущения

- Production URL остаётся стабильным HTTPS tailnet URL и используется как
  Google OAuth callback origin.
- Google Web OAuth client и Yandex bucket/service account/versioning/lifecycle
  создаются вне приложения.
- Все `allowed_tailscale_logins` считаются storage administrators.
- Wizard управляет и evidence replicas, и daily/monthly backup remotes.
- Primary переключается только вручную после полного reconcile.
