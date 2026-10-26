# Router Emulator — Keenetic Virtual Lab

**Виртуальный испытательный стенд для Keenetic, Entware и KAS на базе QEMU.**

Это форк [stavultras/router-emulator](https://github.com/stavultras/router-emulator), а не отдельный
эмулятор, написанный с нуля: базовый эмулятор плат MediaTek (QEMU v10.1.0 с патчами
`qemu-patches/`) разработан upstream. Наш форк добавляет поверх него **экспериментальную
лабораторную обвязку** (`lab/`, часть `main` — слита через PR #1–#3): автоматизированные сценарии,
изолированную сеть, JSON/JUnit-отчёты и проверочные гейты для интеграционных испытаний
Keenetic/Ported, Entware и [Keenetic Auto Setup (KAS)](https://github.com/saymer-alt/keenetic-auto-setup).

Документация: исходная документация upstream —
[English: README.upstream.md](README.upstream.md) · [Русский: README.ru.md](README.ru.md) ·
Сборка: [Linux](README.build.linux.md) · [Windows](README.build.windows.md).

---

## Назначение проекта

Лаборатория заменяет повторяющиеся ручные 30-минутные циклы проверки на физическом роутере
воспроизводимыми автоматическими прогонами:

- тестирование установщика KAS (`install.sh disk`) в реальной среде Entware;
- проверка повторной установки (идемпотентность) и восстановления после прерванной установки;
- проверка обновлений и отката (updater `--force`, rollback после фактического атомарного коммита);
- моделирование сбоев: отключение WAN, сигналы TERM, QEMU reset без ручного перезапуска сервисов;
- испытания uninstall и сохранности чужих ресурсов (посторонний профиль Proxy0, записи cron);
- проверка Entware на USB EXT4, сервисов, DNS-перехвата, правил MARK/CONNMARK, локального SOCKS;
- автоматизация тестов в CI (GitHub Actions) без постоянного использования физических роутеров.

**Лаборатория снижает риск, но не гарантирует идентичность с реальным устройством.**
Аппаратный reboot/потеря питания, Wi-Fi-радио, износ и отключение реального USB, разгрузка
коммутатора, внешний NTP/DNS и VPN-транспорт остаются вне покрытия виртуальных прогонов
и требуют отдельного field acceptance.

## Архитектура

```
GitHub Actions / Test Runner
        ↓
QEMU Router Emulator            (upstream: qemu-patches/ на QEMU v10.1.0)
        ↓
Keenetic/Ported Guest           (Ported 5.1.8 + KeenBOOT KB-41 на пресете Cudy WR3000P)
        ↓
USB EXT4 / Entware              (реальный EXT4, opkg, пиновые пакеты Mihomo/MagiTrickle)
        ↓
KAS Installation / Diagnostics / Recovery   (пиновый KAS 6dd6438e, Doctor, rollback, uninstall)
        ↓
JSON + JUnit + CI Artifacts     (9 гейтов PASS/FAIL/BLOCKED/NOT_TESTED, фиксированные метки)
```

Реальная готовность слоёв (по коду и результатам CI):

- **GitHub Actions / Test Runner** — работает: `lab/run.py`, `lab/test_runner.py`, пять workflow-файлов,
  все в `main`.
- **QEMU Router Emulator** — работает: upstream-эмулятор загружает немодифицированные прошивки
  через реальную цепочку загрузки (BootROM → BL2 → BL31 → U-Boot/KeenBOOT → kernel).
- **Keenetic/Ported Guest** — Ported 5.1.8 на WR3000P загружается до баннера NDMS
  (8 стадий доказаны в CI, PR #2); оригинальная KeeneticOS KN-1010/KN-1012 **не** эмулируется.
- **USB EXT4 / Entware** — доказано в E4-пробах: смонтированный EXT4, рабочий opkg-зеркало,
  реальные DNS-запросы через нативный DNS-прокси.
- **KAS Installation / Diagnostics / Recovery** — жизненный цикл проходит (Doctor 35/0/0),
  кроме uninstall и transaction gate — см. таблицу готовности.
- **JSON + JUnit + CI Artifacts** — работает: три публичных отчёта в артефакте `ported-gates`;
  BLOCKED/NOT_TESTED кодируются как skipped, никогда как успех.

## Поддерживаемые платформы

Базовый эмулятор upstream покрывает (см. [README.upstream.md](README.upstream.md)):

- **MT7621 / MIPS** — `qemu-system-mipsel`, машина `mt7621-router` (например Xiaomi Mi Router 3G,
  Zbtlink ZBT-WG3526);
- **MT7981 / Filogic 820 (ARM)** — машина `mt7981-router` (например Cudy WR3000P/TR3000);
- **MT7986 / Filogic 830**, **MT7987 / Filogic 850**, **MT7988 / Filogic 860/880** — машины
  `mt7986-router`, `mt7987-router`, `mt7988-router`;
- прочие комбинации SPI-NAND/SPI-NOR/eMMC из пресетов upstream.

Лабораторные платформы форка:

- **Ported WR3000P — экспериментальная.** Публичная оценочная прошивка Cudy WR3000P 5.1.8 и
  отдельно опубликованный загрузчик KeenBOOT KB-41 (SHA-256 пиннинг), синтетический NAND
  только для бутстрапа эмулятора, без реального EEPROM. Boot-цепочка и KAS-гейты — часть `main` (слиты PR #2, PR #3).
- **Keenetic KN-1010 / KN-1012** — только как **целевые сценарии сопоставления**. Оригинальная
  KeeneticOS в эмуляции не подтверждена; аппаратные пресеты существуют, E2E не проводилось.

## Текущая готовность

Статусы гейтов — из JSON/JUnit-отчётов лаборатории (`lab/gates.py`: девять гейтов
`BOOT_READY, AUTH_READY, ENTWARE_READY, KAS_PREFLIGHT, KAS_INSTALL, KAS_DOCTOR, KAS_REBOOT,
KAS_ROLLBACK, KAS_UNINSTALL` + дополнительные проверки). Зелёный CI job не «повышает» статус:
`KAS_UNINSTALL=BLOCKED` остаётся BLOCKED, агрегат — BLOCKED.

| Capability | Status | Evidence | Limitation |
|---|---|---|---|
| OpenWrt MT7981: cold boot → LuCI → QEMU reset → повторный boot → LuCI | PASS | CI runs [39828688333](https://github.com/saymer-alt/router-emulator/actions/runs/39828688333), [39828847726](https://github.com/saymer-alt/router-emulator/actions/runs/39828847726) (PR #1) | Только OpenWrt MT7981 |
| Ported WR3000P boot-цепочка: BL2 → BL31 → KeenBOOT → firmware@0x500000 → kernel → SquashFS → баннер NDMS 5.01.C.8.0-2 | PASS | CI run [39839408686](https://github.com/saymer-alt/router-emulator/actions/runs/39839408686) (PR #2), все 8 стадий | Синтетический бутстрап-образ; KB-41 вместо KB-51 (тот отклоняет образ); не реальный EEPROM |
| Аутентифицированный CLI гостя (`show version` и др.) | PASS | `AUTH_READY`, run [39944171291](https://github.com/saymer-alt/router-emulator/actions/runs/39944171291) (PR #3) | Ported-профиль WR3000P |
| Entware на USB EXT4 (opkg, ABI, reboot) | PASS | `ENTWARE_READY`, E4-пробы (run 39944171291) | Пиновые пакеты, изолированное зеркало |
| Нативные возможности (ndmc, RCI, DNS-фильтр, MARK/CONNMARK, TUN, zRAM) | PASS | E4 native capability evidence, `lab/PORTED-E2E.md` | TUN ≠ работающий внешний VPN-туннель |
| KAS: install → Doctor → repeat → reboot → update → rollback (пиновый KAS `6dd6438e`) | PASS | `KAS_INSTALL/KAS_DOCTOR/KAS_REBOOT/KAS_ROLLBACK/KAS_UPDATE`, Doctor 35/0/0, run 39944171291 | Пиновый коммит KAS; изолированное зеркало с lab-CA |
| Прерванная повторная установка (TERM) и QEMU reset с восстановлением контракта | PASS | `KAS_IDEMPOTENCE`, `KAS_INSTALL_INTERRUPTION`, `KAS_WAN_RESTART_GUARD` | Не доказывает восстановление при потере питания |
| Сохранность чужих ресурсов (посторонний Proxy0, cron) | PASS | `KAS_FOREIGN_RESOURCES`, run 39944171291 | — |
| Нативный uninstall KAS | **BLOCKED** | `KAS_UNINSTALL`: `native_remove_not_implemented` | В пиновом KAS удаление не реализовано (`--remove/--purge` возвращают код 3); ручная замена не допускается |
| Нативный transaction gate | **BLOCKED** | `KAS_TRANSACTION_GATE`: `snapshot_gate_blocked` | Продакшн-гейт отвергает `ip global 32767` профиля Ported (допускает `350`); лаборатория сохраняет отказ |
| Оригинальная KeeneticOS (KN-1010/KN-1012) в эмуляции | **NOT_TESTED** | — | Подтверждённого E2E нет |
| Аппаратное field acceptance (потеря питания, Wi-Fi, износ USB, switch offload, внешний NTP/DNS/VPN) | **NOT_TESTED** | `lab/PORTED-E2E.md` → «Acceptance still requiring separate evidence» | Требует физического железа |

Вся лаборатория — часть `main` (слита через PR #1–#3). Контрольная матрица гейтов на
интегрированном дереве: run [39975258142](https://github.com/saymer-alt/router-emulator/actions/runs/39975258142)
— все 9 гейтов и 8 проверок жизненного цикла, `Doctor PASS`; из них `KAS_UNINSTALL=BLOCKED`
(`native_remove_not_implemented`) и `KAS_TRANSACTION_GATE=BLOCKED` (`snapshot_gate_blocked`) —
ожидаемые ограничения, агрегат честно BLOCKED. Прогон [39944171291](https://github.com/saymer-alt/router-emulator/actions/runs/39944171291)
(PR #3) — те же статусы до интеграции.

## Быстрый запуск

Требования: Linux x86_64 (или aarch64), либо Windows; для лабораторной обвязки — Python 3.
Команды приведены из реальной документации upstream и лаборатории.

### 1. Оригинальный upstream-эмулятор

Готовый релиз (без сборки): скачайте `Router-Emulator-<version>-linux-x86_64.tar.gz` и
`Router-Emulator-<version>-presets.zip` со страницы релизов, распакуйте пресеты поверх пакета
(подробности: [README.upstream.md](README.upstream.md), сборка из исходников —
[README.build.linux.md](README.build.linux.md)). Затем:

```bash
tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5   # официальные образы -> nand-wr3000p/
./emulator.sh -P list                           # список пресетов плат
./emulator.sh -P cudy-wr3000p-v1                # консоль роутера в этом терминале
```

**Windows:** скачайте релизный zip и `Router-Emulator-<version>-presets.zip`, распакуйте в одну
папку, запустите `emulator.exe`. NAND-папки создаются через `tools/prepare-nand.sh`
(Linux/WSL) и копируются рядом с `emulator.exe`. Подробности: [README.build.windows.md](README.build.windows.md).

### 2. Лабораторная обвязка форка

Каталог [`lab/`](lab/) — часть `main`. Документация лаборатории:
[`lab/README.ru.md`](lab/README.ru.md) (сценарии и изоляция) и
[`lab/PORTED-E2E.md`](lab/PORTED-E2E.md) (Ported/Entware/KAS E2E и его границы).
Минимальный прогон OpenWrt-сценария на Linux x86_64 (после сборки upstream-эмулятора
и подготовки флеш-папки `tools/prepare-nand.sh`):

```bash
python3 lab/run.py \
  --emulator-root . \
  --scenario lab/scenarios/openwrt-mt7981.json \
  --preset cudy-wr3000p-v1 \
  --nand-dir ./nand-wr3000p \
  --output-dir ./work/lab-reports/openwrt \
  --max-seconds 1500
```

Содержимое flash копируется во временную директорию, исходный образ не изменяется;
WAN = `offline`, LAN = только loopback (`127.0.0.1`).

### 3. CI-сценарии

Вкладка Actions форка:

- **Build/release** (`build.yml`) — на `main`: сборка пакетов upstream и boot-тест пресетов.
- **Keenetic lab - isolated QEMU regression** (`keenetic-lab.yml`) — OpenWrt smoke + unit-тесты.
- **Ported WR3000P - isolated first boot experiment** (`ported-boot-experiment.yml`) — boot-цепочка Ported.
- **Ported authenticated Entware KAS laboratory** (`ported-entware-kas.yml`) — 9 гейтов KAS/Entware,
  отчёты в артефакте `ported-gates`.
- **Ported WR3000P - firmware format inspection** (`ported-image-inspect.yml`) — проверка формата образов.

Лабораторные workflow срабатывают на PR с изменениями `lab/**` и доступны для ручного
запуска (`workflow_dispatch`) из `main`; порядок работы — в
[`lab/PORTED-E2E.md`](lab/PORTED-E2E.md).

## Интеграция с нашими проектами

- [keenetic-auto-setup](https://github.com/saymer-alt/keenetic-auto-setup) — **установщик KAS,
  тестируемый в лаборатории.** Лаборатория исполняет пиновый коммит KAS (`6dd6438e`) нативным
  установщиком Entware и публикует только фиксированные метки результатов; KAS-PR запускают
  кросс-проверку через `workflow_call` с пином на коммит эмулятора. Связанные задачи:
  [roadmap лаборатории #208](https://github.com/saymer-alt/keenetic-auto-setup/issues/208),
  [безопасное удаление `uninstall --remove` #179](https://github.com/saymer-alt/keenetic-auto-setup/issues/179)
  (снятие `KAS_UNINSTALL=BLOCKED` — работа в самом KAS), caller
  [PR #213](https://github.com/saymer-alt/keenetic-auto-setup/pull/213).
- [keenetic-nvr](https://github.com/saymer-alt/keenetic-nvr) — **потенциальный потребитель:**
  сценарии storage/retention и восстановления демонов после сбоев. Полный E2E KNVR в
  лаборатории **не проводился** — подтверждений зелёным прогоном нет.
- [keenwhite](https://github.com/saymer-alt/keenwhite) — **потенциальный потребитель:**
  сценарии владения нативным DNS/firewall и персистентности. Полный E2E KeenWhite
  **не проводился** — результат KAS-гейтов не принимает эти проекты автоматически.

## Безопасность

- Все разрушающие эксперименты выполняются на **одноразовых виртуальных носителях**: NAND/EXT4
  копируются во временные каталоги, исходные образы только читаются.
- Доступа к физическим роутерам нет — ни одного шага с производственным железом без отдельного
  разрешения владельца; WAN лаборатории изолирован (`offline`), LAN — только `127.0.0.1`.
- Не публикуются приватные прошивки, реальные ключи, пароли, дампы NAND, EEPROM/eFuse, токены
  и сырые секретные логи: CI-лог содержит только результаты стадий с фиксированными
  allowlist-метками, гостевой текст и UART остаются приватными; артефакты исключают firmware,
  flash, учётные данные и raw UART/SSH.
- Виртуальные результаты **не заменяют field acceptance** на физическом устройстве.

## Происхождение и лицензии

- Базовый эмулятор разработан upstream: [stavultras/router-emulator](https://github.com/stavultras/router-emulator)
  (набор патчей QEMU v10.1.0 и инструменты). Текущая база `main` — **upstream 4.0**
  (`3ae3e14`, merge PR #33; пост-релизные коммиты upstream без тега не догоняются —
  см. [VERSION](VERSION)); лабораторная обвязка `lab/` — часть `main` (слита PR #1–#3).
  Исходная документация сохранена без изменений:
  [README.upstream.md](README.upstream.md) (English), [README.ru.md](README.ru.md) (Русский).
- Этот форк добавляет экспериментальную лабораторную инфраструктуру и автоматизированные
  испытания (каталог `lab/`); история Git и авторство upstream сохранены. Матрица гейтов
  на интегрированном дереве — run [39975258142](https://github.com/saymer-alt/router-emulator/actions/runs/39975258142);
  `external_swap` и физическое железо остаются NOT_TESTED, `KAS_UNINSTALL` и
  `KAS_TRANSACTION_GATE` — BLOCKED до реализации в самом KAS.
- Лицензия — **GPL-2.0-or-later**, как у QEMU: см. [LICENSE](LICENSE). Образы прошивок не входят
  в репозиторий; OpenWrt и вендорские прошивки сохраняют собственные лицензии.
- Синхронизация с upstream — через отдельную ветку и PR по процедуре
  [docs/UPSTREAM-SYNC.md](docs/UPSTREAM-SYNC.md), без автоматического слияния, с сохранением
  GPL-2.0, авторства и исходного кода.

## Ошибки, предложения и обратная связь

- **Ошибка** — [форма Bug report](https://github.com/saymer-alt/router-emulator/issues/new?template=bug_report.yml)
- **Предложение** — [форма Feature request](https://github.com/saymer-alt/router-emulator/issues/new?template=feature_request.yml)
- **Свободный вопрос** — [обычный Issue без шаблона](https://github.com/saymer-alt/router-emulator/issues/new)
- **Уязвимость** — только приватно через [Report a vulnerability](https://github.com/saymer-alt/router-emulator/security/advisories/new); не публикуйте технические детали в Issues.

Перед публикацией обезличивайте материалы: без паролей, токенов, закрытых ключей, подписок, VPN-ключей и приватных IP-конфигураций.
