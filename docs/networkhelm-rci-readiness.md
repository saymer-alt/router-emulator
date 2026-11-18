# NetworkHelm ↔ Ported KeeneticOS: подготовительная проверка RCI

**Связано с [issue #110](https://github.com/saymer-alt/router-emulator/issues/110).**
Состояние: **подготовлен и локально протестирован скрипт**, живой QEMU+RCI и GUI NetworkHelm **НЕ ПРОВЕРЕНЫ**.

## Зачем

NetworkHelm v1.3.0 ([upstream](https://github.com/vasmarfas/networkhelm/releases/tag/v1.3.0)) поддерживает Keenetic/Netcraze по RCI, но поддержка проверялась автором на KN-1221. Наш MT7621 Ported 5.1.8 уже имеет гостевое NDMS HTTP, проверенное на пробросе `127.0.0.1:8080`, и CLI `show version`: см. [матрицу MT7621](../lab/mt7621/README.md). Это ещё не подтверждает доступность защищённого RCI или совместимость с NetworkHelm.

Скрипт [`lab/networkhelm_rci_probe.py`](../lab/networkhelm_rci_probe.py) проверяет только **следующий маленький этап**: TCP/HTTP на loopback, стандартную авторизацию `/auth` с MD5+SHA256 challenge по схеме KeeneticOS, и **GET** `/rci/show/version`, `/rci/show/system`, `/rci/show/interface`, а также один фиксированный **POST** `/rci/` со списком `[{"show":{"version":{}}},{"show":{"system":{}}},{"show":{"interface":{}}}]` с проверкой JSON, структуры батч-ответа и RCI status. Никаких команд изменения конфигурации не посылает. Разрешены только два типа POST: вход на `/auth` и пакет исключительно читающих `show`-команд на `/rci/`. Пакетные RCI-запросы используются NetworkHelm при построении окон; произвольный POST пользователь передать не может.

## Запуск без QEMU (локальные unit-тесты)

Из корня репозитория:

```bash
python3 -m unittest lab/test_networkhelm_rci_probe.py -v
```

Для работы скрипта нужны только библиотеки Python 3 из стандартной поставки. Никакие зависимости NetworkHelm, Entware и Java не нужны.

## Запуск с будущим гостем QEMU

1. Запустить **изолированный disposable MT7621 Ported-гость** по действующей инструкции стенда, с локальным host-forward HTTP на `127.0.0.1:8080` (не открывать наружу). Убедиться в настоящем `system ready` и получить одноразовые лабораторные логин и пароль, если `/auth` требует входа.
2. На **хосте** (не в роутере) выполнить:

```bash
# Без учётных данных: показывает, нужен ли login, не угадывает пароль.
python3 lab/networkhelm_rci_probe.py --url http://127.0.0.1:8080

# Если тестовый гость требует авторизацию, укажите credentials
# через переменные окружения, НЕ через аргументы командной строки:
export NETWORKHELM_RCI_USER='lab-user'
read -r -s -p 'Lab RCI password: ' NETWORKHELM_RCI_PASSWORD
echo
export NETWORKHELM_RCI_PASSWORD
python3 lab/networkhelm_rci_probe.py --url http://127.0.0.1:8080
unset NETWORKHELM_RCI_USER NETWORKHELM_RCI_PASSWORD
```

На Windows PowerShell:

```powershell
py -3 lab/networkhelm_rci_probe.py --url http://127.0.0.1:8080
```

Не писать реальный пароль в отчёте, команде CLI, issue или workflow. Для теста создать только лабораторные credentials.

## Значение результатов

| Проверка | Что доказывает | Чего не доказывает |
|---|---|---|
| `HTTP_REACHABLE=PASS` | На localhost доступен HTTP | Что это NDMS/RCI или что браузерный клиент подключится |
| `AUTH=PASS` | `/auth` доступен, а challenge-login (при наличии credentials) принят | Что все привилегии получены |
| `AUTH=BLOCKED` | Сервер запросил логин, а тестовые credentials не переданы | Никакой неисправности NetworkHelm |
| `RCI_*=PASS` | Три read-only GET метода ответили HTTP 200 с JSON без стандартного RCI error-status | Полную совместимость меню, динамики, GUI, SFTP, SSH, редактирования |
| `RCI_BATCH_SHOW=PASS` | Read-only POST `/rci/` вернул массив из трёх `show`-ответов в ожидаемом порядке без RCI ошибок | Реакцию всех окон NetworkHelm или реальный GUI |
| `FAIL` | Конкретное препятствие, указанное в `reason` | Не обязательно баг NetworkHelm |
| `guest_e2e=false` | Скрипт сам не удостоверяет происхождение HTTP-сервера и цепочку загрузки гостя | Не переименовывать в `guest_e2e=true` без внешних доказательств |

Скрипт не принимает HTTP-цели вне loopback (IPv4/IPv6 или localhost), **не следует HTTP redirect** и не публикует response body, cookie, пароли, hash или имена интерфейсов. Публичный JSON содержит только метки и коды. Чувствительные гостевые логи остаются приватными.

Выходные коды: **0** PASS, **1** FAIL, **2** BLOCKED. Статус `PASS` относится **только** к этому read-only RCI smoke и никогда автоматически не закрывает issue #110.


## Автоматическая связка с реальным QEMU/Ported (новое)

Добавлен [`lab/networkhelm_qemu_smoke.py`](../lab/networkhelm_qemu_smoke.py): он вызывает **существующий** `lab.run.run` со сценарием `lab/scenarios/ported-mt7621-cli.json` и подключает read-only проверку через уже предусмотренный в раннере `after_scenario` hook.

**Поведение:** в disposable NAND загружается виртуальный MT7621 с Ported, подтверждаются нативные NDMS `system ready`, telnet CLI `show version` и `show system`; **после этого, пока QEMU ещё работает**, выполняется HTTP/RCI probe на localhost:8080. После проверки QEMU выключается и публикуются отдельные санитизированные JSON с итогами гостя и RCI. При провале CLI запросы RCI **не выполняются**. Никаких записей в настройки NDMS.

Пример для Linux x86_64 / WSL2, **только когда уже готовы QEMU с MT7621-патчами и частный NAND Ported 5.1.8**:

```bash
# Проверить, что localhost 8080 и 8023 никем не заняты.
# Пример при готовом patched QEMU:
export QEMU_BIN=/private/qemu-mipsel/qemu-system-mipsel

python3 -m lab.networkhelm_qemu_smoke \
  --emulator-root . \
  --nand-dir /private/nand-mi-router-3g-ported \
  --output-dir /private/reports/networkhelm-mt7621-first \
  --max-seconds 1800

# Не публиковать исходные UART/SSH/NAND. Для обзора:
cat /private/reports/networkhelm-mt7621-first/networkhelm-summary.json
cat /private/reports/networkhelm-mt7621-first/networkhelm-rci.json
```

Пути `/private/...` — **заполнители**, не обещание, что соответствующие образы присутствуют в рантайме. Точный путь к образу нужно взять из локального стенда, не скачивать и не публиковать приватный NAND.

При требовании авторизации RCI передать `--credentials-file /private/lab-credentials.json` (JSON с ключами `username` и `password`, права **0600** на Linux). Формат credentials дополнительно проверяет существующий раннер. Без файла `AUTH=BLOCKED` при HTTP 401, а не ложный PASS.

**Фиксированный проброс:** `127.0.0.1:8080 → guest:80`, `127.0.0.1:8023 → guest:23`. Если любой локальный порт занят, обвязка не запускает гостя. В нашей текущей изолированной исполнительной среде порт **8080 занят сторонним HTTP-сервисом**, а QEMU и NAND Ported отсутствуют: реальный прогон здесь **BLOCKED**, нельзя считать этот HTTP-сервис эмулятором.

Артефакты:
- `report.json`, `junit.xml` — исходный отчёт раннера, только boot/CLI.
- `networkhelm-rci.json` — статусы read-only RCI (создаётся лишь после пройденного boot/CLI).
- `networkhelm-summary.json` — разделение `guest` и `rci`; если нет подтверждённого гостя, RCI маркируется `NOT_TESTED`.
- **Выходной код**: 0 — оба этапа PASS; 1 — гостевая CLI прошла, но RCI FAIL; 2 — BLOCKED / нет подтверждённого гостя или учётных данных.

Локальная тестовая обвязка [`lab/test_networkhelm_qemu_smoke.py`](../lab/test_networkhelm_qemu_smoke.py) проверяет preflight-отказ при занятом порте, приватность credentials, корректную передачу `after_scenario`, ложный PASS, BLOCKED, сохранение раздельных отчётов. Прогоны **только с мокированным жизненным циклом QEMU**, не являются guest E2E.


## Следующий этап после read-only RCI PASS

- Запустить NetworkHelm как десктопное приложение на Linux-хосте, в headless-среде Xvfb (или использовать подготовленный бинарник). **Не устанавливать GUI на MIPSel/Entware**.
- Подключить к изолированному гостю и проверить реальные окна и таблицы, System Dashboard, DNS, маршруты, WireGuard и журнал.
- При блокировке MT7621 пробовать ARM64 Ported/MT7981 для дифференциальной диагностики, не смешивая результаты.
- Каждой реальной проверке присваивать PASS/FAIL/BLOCKED/NOT_TESTED с доказательством. Ограничение: не более трёх осмысленных попыток на неисправность. Секреты и raw UART/SSH никуда не загружать.
- Никаких настроек на реальном оборудовании; no GUI auto-save/firewall changes; у NetworkHelm Keenetic нет Safe Mode. Все будущие write-тесты только в одноразовом госте.
- Отдельно изучить полезные для KAS WEB UI/UX-приёмы без копирования кода, учитывая лицензию NetworkHelm PolyForm Noncommercial.

## Тесты и факты на дату подготовки (18.11.2026)

Локально выполнены **16/16 unit-тестов PASS** (9 проверок RCI + 7 проверок QEMU-обвязки) с временным loopback HTTP-сервером: корректная challenge-аутентификация и чтение, фиксированный batch `show`, отсутствие пароля → BLOCKED, неверный пароль → FAIL без утечки, потеря сессии, невалидный JSON/пустой JSON, ошибка RCI в ответе HTTP 200, неверная форма пакетного ответа, отказ/отсутствие batch API, открытая авторизация, блокировка внешних адресов. Отдельные варианты batch проверяются внутри одного параметризованного теста. Тесты **не запускали QEMU** и **не подтверждают полевую работоспособность на KeeneticOS**. Issue [#110](https://github.com/saymer-alt/router-emulator/issues/110) остаётся открытым.
