# Локальный стенд KAS на домашнем Windows-ПК — первый этап

> **Статус:** только read-only проверка существующего Windows Router Emulator и план
> безопасного локального запуска. Это **не** подтверждение, что KAS уже работает
> в Windows GUI, и **не** готовый fast-uninstall snapshot runner.
> [Бэклог оптимизации KAS #214](https://github.com/saymer-alt/keenetic-auto-setup/issues/214).

## Что уже есть

- **Windows Router Emulator**: `emulator.exe` запускает виртуальные платы через
  `qemu/qemu-system-aarch64.exe`. Его штатная инструкция ориентирована прежде
  всего на **OpenWrt**. В версии Windows launcher NAND-папка меняется гостем
  *на месте*, поэтому резервная копия до любых экспериментов обязательна.
- **Нативная лаборатория KAS**: `lab/ported_e2e.py` и `lab/run.py` выполняют
  реальный Keenetic **Ported 5.1.8 на Cudy WR3000P/MT7981** и Entware. Сейчас
  это Linux-сценарии, использующие **отдельный QEMU v10.1.0 с FE IRQ-патчами**
  (`lab/build_qemu.sh`). Наличие Windows бинарника не доказывает наличия тех
  же патчей и совместимости с Ported.
- В `lab/run.py` уже есть остановка QEMU через monitor и снимок NAND + EXT4
  (`context['snapshot']`); `lab/ported_e2e.py` создаёт приватные снимки.
  Текущий `before-kas` — **перед установкой**, а не готовый образ для
  мгновенного удаления. Для ускорения понадобится отдельный честный
  **post-install + Doctor PASS** эталон. Не считать его готовым заранее.

## 1. Проверка установленного Windows-эмулятора

Открой **PowerShell**, клонируй / скачай актуальный репозиторий отдельно
от распакованного приложения (без переноса личных NAND/секретов в Git), затем:

```powershell
# Пример пути: подставь каталог, где уже лежит emulator.exe
$EmulatorRoot = 'D:\Router-Emulator\Router-Emulator'

# Запускается ИЗ корня клона saymer-alt/router-emulator:
powershell.exe -NoProfile -File .\local-windows-preflight.ps1 -EmulatorRoot $EmulatorRoot
wsl --status
wsl --list --verbose
```

`local-windows-preflight.ps1` **ничего не устанавливает, не запускает и не
изменяет**. Он проверяет наличие GUI, QEMU, пресетов, NAND-каталога и команды
WSL; считает SHA256 Windows QEMU. **Не выводить сюда** конфиги домашнего
Keenetic, `credentials`, дампы/EEPROM, приватные журналы и ключи.

Если приложение уже работает, **не переустанавливай его**, а используй
только для отдельной проверки **OpenWrt** на тестовой NAND-копии:
выключить виртуальный роутер → создать локальную копию его NAND-папки →
выбрать **копию** в пресете → WAN = отключено/offline, LAN = «только этот ПК».
Отключи **Bridge to adapter**, передачу физических USB и любой доступ к
действующим Keenetic. Проверять можно загрузку и локальный HTTP.
Это **не тест KAS** и **не Keenetic Ported acceptance**.

## 2. Для KAS нужен Linux backend; Windows остаётся хостом

Рекомендуемый путь — **WSL2 Ubuntu** (или изолированная Linux VM) при наличии
поддержки виртуализации. Текущий автоматический сценарий использует
`emulator.sh`, UNIX sockets, Bash и POSIX-утилиты, а не `emulator.exe`.

- Не запускать новую сборку Windows-эмулятора: она здесь не нужна.
- В WSL2 использовать **Linux filesystem** (`~/keenetic-lab`), не
  `/mnt/c/...` для больших NAND/EXT4 и UNIX-сокетов.
- Подготовить однажды проверенные QEMU + assets + Ported NAND и тестовый
  Entware с **только синтетическими** учётными данными. Пиновать SHA и
  проверять хеши, как в `lab/build_qemu.sh` и `lab/fetch_lab_assets.sh`.
  Не использовать реальные flash dumps и пароли.
- Первый полный нативный прогон делать существующим `lab.ported_e2e`
  после устранения текущего лабораторного FAIL; сохранить только приватные
  файлы локально. Сначала проверить `BOOT_READY`, `AUTH_READY`,
  `ENTWARE_READY`, `KAS_INSTALL` и `KAS_DOCTOR`.
- В отдельном небольшом PR после текущего Z AI-agent добавить
  **post-install эталон** и **targeted fast-uninstall** на одноразовых
  NAND+OPT-копиях с независимым `PASS/FAIL/BLOCKED`. После каждого теста
  уничтожать клон, **не** исходный эталон. Процедура подготовки эталона
  должна гарантировать `sync` и согласованную остановку дисков QEMU.
- Пересобирать QEMU только после смены upstream/patched-source; при правке
  лишь KAS scripts **не** пересобирать QEMU и **не** проходить весь bootstrap.
  OpenWrt/QEMU regression — при изменениях QEMU/board; полный KAS E2E —
  контрольная приёмка до merge/release.

## 3. Ожидания по скорости и приватности

[Ранний boot-run 39975253995](https://github.com/saymer-alt/router-emulator/actions/runs/39975253995)
показал около 14 секунд для первых восьми UART этапов, без SSH/Entware.
Примерно 37 секунд длилась **вся короткая job**. Это не доказательство
готовности установленного KAS через 14, 27 или 37 секунд.
Полный [KAS run 40102040574](https://github.com/saymer-alt/keenetic-auto-setup/actions/runs/40102040574)
занимал около 19 минут. Реальную скорость fast-uninstall нужно измерить
локально после реализации.

На домашнем ПК не включать публичный self-hosted runner для чужих PR.
Любые гостевые образы, пароли, виртуальный USB, UART и снапшоты оставлять
локально, не в GitHub artifacts/cache. Проброс LAN строго loopback/offline.
Для рабочего ПК необходимы отдельное разрешение и отделение от
корпоративной сети; начинать с домашнего.

## Следующее практическое действие

Сначала запустить **только read-only PowerShell preflight** и определить,
готовы ли текущий Windows пакет и WSL2. После этого — точечная настройка
Linux backend, без изменения рабочего эмулятора и без длинных облачных CI.

## PowerShell preflight: копируем скрипт в файл локально

Скрипт в виде исходного текста для проверки, **без** GitHub Actions и автозапуска:

```powershell
# Requires Windows PowerShell 5.1 or PowerShell 7.
# Read-only inventory. Does not launch QEMU, WSL, download images or print credentials.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EmulatorRoot
)
$ErrorActionPreference = 'Stop'
try {
    $root = (Resolve-Path -LiteralPath $EmulatorRoot -ErrorAction Stop).Path
} catch {
    Write-Error 'EmulatorRoot does not exist. Point it to the folder that contains emulator.exe.'
    exit 2
}

function Show-Check {
    param([string]$Name, [bool]$Found, [string]$Detail = '')
    $status = if ($Found) { 'OK' } else { 'MISSING' }
    Write-Output ('[{0}] {1}{2}' -f $status, $Name, $(if ($Detail) { ': ' + $Detail } else { '' }))
}

$launcher = Test-Path -LiteralPath (Join-Path $root 'emulator.exe') -PathType Leaf
$qemu = Test-Path -LiteralPath (Join-Path $root 'qemu\qemu-system-aarch64.exe') -PathType Leaf
$presets = Test-Path -LiteralPath (Join-Path $root 'presets') -PathType Container
$nand = Test-Path -LiteralPath (Join-Path $root 'nand-wr3000p') -PathType Container
$wsl = $null -ne (Get-Command 'wsl.exe' -ErrorAction SilentlyContinue)

Write-Output 'Router Emulator / Windows — local read-only preflight'
Show-Check 'Windows GUI emulator.exe' $launcher
Show-Check 'Windows QEMU aarch64 binary' $qemu
Show-Check 'Board presets directory' $presets
Show-Check 'WR3000P NAND directory (optional)' $nand 'presence does not prove firmware identity'
Show-Check 'WSL command available' $wsl 'presence does not prove WSL2 Linux is configured'

if ($qemu) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $root 'qemu\qemu-system-aarch64.exe') -Algorithm SHA256).Hash
    Write-Output ('[INFO] Windows QEMU SHA256: ' + $hash)
}
Write-Output '[INFO] No configuration, flash, router, private key, or snapshot has been read.'
Write-Output '[INFO] This preflight proves files exist; it does NOT prove Ported boot, FE IRQ patch, Entware, or KAS acceptance.'
Write-Output '[NEXT] With the emulator powered off, use the Windows GUI on disposable OpenWrt NAND only.'
Write-Output '[NEXT] Keep WAN offline and LAN local-only; never bridge to a real home/work adapter.'
Write-Output '[NEXT] Run wsl --status manually to check WSL2 for the existing Linux native KAS lab.'

if (-not ($launcher -and $qemu -and $presets)) { exit 2 }
exit 0

```

Сохрани содержимое блока в `local-windows-preflight.ps1` рядом с каталогом клона репозитория или используй отдельно предоставленный файл; затем выполни команду из раздела 1. Не запускать вместе с действующим виртуальным роутером.
