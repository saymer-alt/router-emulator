# Router Emulator (MediaTek MT7981B / Filogic 820, MT7986 / Filogic 830, MT7987 / Filogic 850, MT7988 / Filogic 860 / 880, MT7621)

[English](README.md) · **Русский** · Сборка: [Linux](README.build.linux.ru.md) · [Windows](README.build.windows.ru.md)

> **С версии 3.6 пресеты плат — отдельный архив.** В пакетах больше нет
> `presets/`. В каждом релизе рядом с пакетами лежит
> `Router-Emulator-<версия>-presets.zip` (на GitHub, Google Диске и
> Яндекс.Диске): распакуйте его поверх пакета. Если меняются только пресеты,
> выходит релиз только с пресетами `X.Y.Z` (например, 3.6.1) — новый архив
> пресетов для пакетов X.Y.

**Скачать:** [Router Emulator на Google Диске](https://drive.google.com/drive/folders/173WnINcit9_lMt86FjfKkGMqnZrCZ-Am)
или [на Яндекс.Диске](https://yadi.sk/d/Re-w1Nm1DBFhOw): пакеты последнего
релиза, его архив пресетов (`Router-Emulator-<версия>-presets.zip`)
и готовые папки флеша, по архиву на папку
(`Router-Emulator-<папка>.zip`, например `Router-Emulator-nand-wr3000p.zip`),
для всех пресетов, кроме Keenetic KN-1012 и Routerich AX3000 v1 (им нужны
дампы собственного роутера, такие папки не собираются). Распакуйте поверх
пакета архив пресетов и папки нужных плат.

QEMU-машина `mt7981-router`, эмулирующая плату роутера на MT7981B на
уровне железа. Железо платы (PHY/коммутатор Ethernet, флеш, тип и размер
RAM, USB) настраивается, поэтому одна машина покрывает много устройств;
конкретные роутеры описываются **пресетами** (`presets/*.ini`). Прошивки
запускаются **без изменений**, по той же цепочке загрузки, что и на
настоящем устройстве:

```
BootROM (эмулирован) → BL2 (preloader MediaTek, калибровка DDR) → BL31 (TF-A)
→ U-Boot → OpenWrt (ядро + rootfs из UBI на SPI-NAND)
```

Загружаются и собственный загрузчик OpenWrt (разметка «ubootmod»), и
стоковые загрузчики производителя (BL2/FIP, снятые с настоящих устройств,
с NMBM). Всё, что для этого понадобилось, сделано на стороне эмулятора;
прошивки не патчатся.

Платы на MT7986A/B (Filogic 830) работают на второй машине,
`mt7986-router` (ключ пресета `soc=mt7986`): те же периферийные блоки,
четыре ядра, BL2 для MT7986 со своей калибровкой DRAM и определением её
размера, см. [MT7986](#mt7986-filogic-830); платы на MT7987A/B — на
`mt7987-router` (`soc=mt7987`, см. [MT7987](#mt7987-filogic-850)); платы на
MT7988A/D (Filogic 880 / 860) — на `mt7988-router` (`soc=mt7988`, см.
[MT7988](#mt7988-filogic-880--860)); платы на MT7621A (MIPS) — на
`mt7621-router` в `qemu-system-mipsel` (`soc=mt7621`, см.
[MT7621](#mt7621-mips)).

## Быстрый старт

Linux: [README.build.linux.ru.md](README.build.linux.ru.md) (или
`Router-Emulator-<версия>-linux-x86_64.tar.gz` из релиза: QEMU со
своими библиотеками, собирать не нужно; распакуйте поверх него
`Router-Emulator-<версия>-presets.zip` из того же релиза), затем

```bash
tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5   # официальные образы -> nand-wr3000p/
./emulator.sh -P list                             # пресеты плат
./emulator.sh -P cudy-wr3000p-v1                  # консоль роутера в этом терминале
```

Windows: скачайте zip из релиза (или соберите сами —
[README.build.windows.ru.md](README.build.windows.ru.md)) и
`Router-Emulator-<версия>-presets.zip` из того же релиза, распакуйте оба в
одно место, запустите `emulator.exe`. Папок NAND в релизе нет: соберите их
`tools/prepare-nand.sh` (Linux или WSL) и положите рядом с `emulator.exe`
или используйте дампы с настоящего роутера.

64-битный Linux на Arm (Raspberry Pi 4/5, Arm-серверы, ТВ-приставки с
CoreELEC / LibreELEC): `Router-Emulator-<версия>-linux-aarch64.tar.gz`.
В нём своя библиотека C и статический `bash`, поэтому нужно только ядро
aarch64: пакет работает и с 32-битным userland (`uname -m` выдаёт `aarch64`
или `armv8l`; с 32-битным ядром ARMv7 — нет: QEMU 10 эмулирует 64-битные
гостевые системы только на 64-битных хостах). Папки флеша соберите на ПК и
скопируйте, затем `./bash emulator.sh -P ... -w user -l user`, если `bash`
в системе — это busybox. Приставка на Amlogic S905 (4× Cortex-A53, 1 ГБ)
загружает WR3000S до LuCI примерно за 8 минут.

## Железо платы (параметры машины)

`-M mt7981-router,nand-dir=ПАПКА,<параметры>` (или `mt7986-router`) и
`-m <размер RAM>` (128M, 256M, 512M, 1G или 2G; 128 МБ не проверены: нет
BL2, на котором попробовать, — будем рады отзывам):

| Параметр | Значения | Смысл |
|---|---|---|
| `gmac0` | `mt7531` · `an8855` · `mt7988-switch` · `rtl8221b` · `yt8821` · `gpy211` · `en8811h` · `none` | что подключено к GMAC0 (mac@0, SGMII0); `an8855` — коммутатор Airoha AN8855 на MDIO 1 (CPU-порт 5, например Routerich BE7200); `mt7988-switch` — встроенный 4-портовый коммутатор MT7988; `en8811h` — 2.5G PHY Airoha EN8811H (OpenWrt One) |
| `ports` | например `wan:lan1:lan2:lan3:lan4` | id netdev портов 0..4 MT7531 и порта 5 при `port5=` (`-` — не используется) |
| `port5`, `port5-phy-addr`, `port5-reset-gpio` | `rtl8221b` · `yt8821` · `gpy211` · `none`; адрес MDIO (5); GPIO | 2.5G PHY на порту 5 MT7531 (SGMII), например Netcore N60 Pro; его netdev — шестой элемент `ports` |
| `gmac0-port`, `gmac1-port` | id netdev | порт PHY, подключённого прямо к GMAC |
| `gmac1` | `rtl8221b` · `yt8821` · `gpy211` · `en8811h` · `gphy` · `i2p5ge` · `mt7531-p5` · `none` | GMAC1 (mac@1); `gphy` — встроенный 1G PHY MT7981; `i2p5ge` — внутренний 2.5G PHY MT7987 / MT7988; `gpy211` — MaxLinear GPY211C; `mt7531-p5` — SGMII-связь с портом 5 MT7531 (референсная схема MediaTek, например SNR-CPE-AX2: заводская прошивка ведёт через неё WAN; нужны `gmac0=mt7531` и порт 5 без PHY) |
| `gmac0-reset-gpio`, `gmac1-reset-gpio` | GPIO, `-1` (по умолчанию) | линия аппаратного сброса 2.5G PHY; по умолчанию не подключена (отсутствие линии безвредно, неверная могла бы держать PHY в сбросе) |
| `gmac0-phy-addr`, `gmac1-phy-addr` | адрес MDIO | адрес этого PHY (по умолчанию 1 на gmac0, 6 на gmac1) |
| `flash` | `nand` · `nor` · `emmc` | загрузочный флеш: SPI-NAND на SPI0 (по умолчанию), SPI-NOR на SPI2 (MT7986: SPI0) или eMMC (см. ниже) |
| `nor-nand` | `on` | SPI-NOR **и** SPI-NAND (Teltonika RUTC50, OpenWrt One): в папке флеша файлы разделов NOR, в её подпапке `nand` — файлы NAND; `flash=nor` / `flash=nand` выбирает загрузочный флеш (см. [SPI-NOR + SPI-NAND](#spi-nor--spi-nand-teltonika-rutc50-openwrt-one)) |
| `nand` | `128` · `256` · `512` | SPI-NAND W25N01GV / W25N02KV / W25N04KV |
| `nand-id` | от 2 до 8 байт в hex | байты, которыми SPI-NAND отвечает на READ ID, дальше они повторяются (по умолчанию ID Winbond, например `efaa21`); `c8017f7f7f` — ESMT F50L1G41LB, микросхема, например, Cudy WR3000P / WR3000E. Работает микросхема как эмулируемая; заводская прошивка может проверять ID (MT7621: READ ID параллельной NAND, см. ниже) |
| `nand-spi` | `0` (по умолчанию) · `1` · `2` | контроллер SPI, на котором SPI-NAND (BPI-R4 Lite: 2) |
| `switch-irq-gpio` | GPIO | EINT прерывания MT7531 (по умолчанию 38 / 66 / 41 для MT7981 / MT7986 / MT7987) |
| `flash=emmc`, `emmc-boot` | МБ (4) | вместо этого eMMC на MSDC0: один образ (первый `*.img` в папке флеша или `-drive if=sd`) = boot0 + boot1 (по `emmc-boot` МБ) + пользовательская область, размер — степень двойки |
| `nor`, `nor-id` | МБ (16), JEDEC ID (`ef4018`) | размер и ID SPI-NOR, например `204018` = XMC XM25QH128C/D, `c84018` = GD25Q128; `nor=32,nor-id=ef4019` = Winbond W25Q256JV, `nor=64,nor-id=ef4020` = Winbond W25Q512JV (больше 16 МБ — 4-байтовая адресация; например, WR3000 с перепаянной микросхемой). MT7621: 16, 32 или 64 МБ, без `nor-id` — микросхема Winbond этого размера |
| `nor-uid` | 16 hex-цифр | только MT7621: уникальный ID SPI-NOR (команда 4b, по умолчанию нет); стоковая прошивка Cudy на NOR-платах (M1300 v2) сверяет ID и уникальный ID микросхемы (`/proc/sys/dev/flash_uuid`, например `2040182040-` + 16 цифр) с bdinfo, без совпадения пароль администратора нельзя ни задать, ни проверить: задайте в `nor-id` ID микросхемы, а в `nor-uid=` — 16 цифр после `-` |
| `ddr` | `ddr3` · `ddr4` | тип распаянной памяти: BL2, собранный для другого типа, останавливает машину, как не проходит инициализация DRAM на настоящей плате |
| `usb-port` | `none` · `2` · `3` | разъём USB (USB 3.0: устройства подключаются на SuperSpeed) |
| `usb-host` | `VID:PID[;VID:PID…]` | проброс USB-устройств этого ПК в роутер (корневые порты USB 2.0 №2, 3, …, в том числе на платах без разъёма), например Wi-Fi-донгла, LTE-модема, USB-Ethernet; см. «USB Wi-Fi-донгл» |
| `pcie-wifi` | `none` · `mt7992` | MT7987 / MT7988: карта в слоте PCIe. `mt7992` — MediaTek MT7992, Wi-Fi 7 (2,4 + 5 ГГц); OpenWrt работает с ней через `kmod-mt7996e` и прошивки `kmod-mt7992-firmware`, которых нет в официальных образах MT7987 |
| `reset-gpio`, `wps-gpio` | GPIO | кнопки (QOM `/machine/pinctrl` `reset-button`, `wps-button`) |
| `reset-active-high`, `wps-active-high` | `on` | кнопка читается как 1 при нажатии (по умолчанию active low) |
| `reset-hold` | мс | включение с зажатым reset (TFTP recovery в U-Boot) |
| `pan` | `on` (по умолчанию) · `off` | сообщать гостю PAN (Privileged Access Never) в ID-регистрах процессора. У ядер A53 / A73 его нет, поэтому ядро OpenWrt эмулирует PAN, переключая TTBR0 на каждом системном вызове, а в TCG каждое переключение сбрасывает TLB; с `pan=on` ядро использует PAN, который QEMU и так реализует: системные вызовы в 2,5–3 раза быстрее, загрузка тратит ~40 % меньше процессора хоста. `off`: собственные ID-регистры ядер |
| `poweroff` | `stop` · `reboot` | `stop` (по умолчанию): эмулятор завершается, когда Linux выключается («reboot: Power down» в UART0); `reboot`: как настоящая плата, прошивка которой не умеет выключаться |
| `gpio-log` | `on` | печать изменений выходов GPIO и состояний светодиодов |
| `led1` … `led32` | `ИМЯ: ЦВЕТ ИСТОЧНИК [+ ЦВЕТ ИСТОЧНИК…]` | светодиоды передней панели (см. ниже); `led-state` (QOM `/machine`) отдаёт их состояние |
| `efuse` | файл | загрузить содержимое eFuse из дампа настоящей платы (до 4 КБ, как читается из `/sys/bus/nvmem/devices/nvmem0/nvmem`), чтобы калибровки и данные чипа совпадали с этой платой |
| `efuse-uid` | 32 hex-цифры | задать уникальный блок чипа, чтобы несколько эмулированных плат не были одинаковыми |
| `nand-uid` | 32 hex-цифры | уникальный ID SPI-NAND (по умолчанию выводится из имени папки флеша); стоковая прошивка Cudy сверяет ID микросхемы и уникальный ID (`/proc/sys/dev/flash_uuid`) с `fuuid` из bdinfo (`C801-` + уникальный ID + его дополнение), без совпадения вход закрыт («Invalid board info!»): задайте `nand-id=c8017f7f7f` и в `nand-uid=` 32 hex-цифры после `C801-` |

Светодиоды передней панели: `led1=Status: red gpio:11:low + white gpio:10:low` —
одна лампа двух цветов, у каждого цвета свой GPIO (`:low` = GPIO_ACTIVE_LOW).
Источники: `gpio:N[:low]` (GPIO SoC), `phy:ADDR:N` (вывод LED N у Ethernet
PHY с адресом MDIO ADDR, как его запрограммировали регистры LED: RTL8221B,
GPY211; например, лампа WAN у WR3000P в актуальном OpenWrt), `swphy:PORT:N`
(вывод LED N у PHY порта PORT встроенного коммутатора MT7988, например лампы
портов BPi-R4), `ws2812:BUS:N`
(цепочка RGB WS2812B на шине SPI BUS, цвет `rgb`: Redmi AX6000), `hc595:BUS:N`
(выход N цепочки сдвиговых регистров 74HC595 на шине SPI BUS, драйвер Linux
gpio-74x164: светодиоды мобильной сети, SIM и сигнала Teltonika RUTC50) и
`pwm:N` (канал PWM, pwm-leds: BPi-R4 Lite, OpenWrt One). QEMU опрашивает их каждые 50 мс;
`qom-get /machine led-state` выдаёт по каждой лампе (`;`) и цвету (`,`)
`0`, `1`, `b` (мигает) или `#rrggbb` / `b#rrggbb`. Windows-лаунчер
показывает их рядом лампочек над консолью; с `-g` (`gpio-log=on`) QEMU
печатает каждое изменение («LED Status: white on»).
[`tools/dts-leds.py`](tools/dts-leds.py) заполняет ключи `led` пресетов из
device tree OpenWrt (`--write --all`).

Сетевые порты — netdev QEMU с id из таблицы (`wan`, `lan1`, …).
Ключи пресета только для лаунчеров: `lan-ip` (адрес роутера в LAN, по
умолчанию 192.168.1.1) и `lan-forwards` (по умолчанию
`8080:80,8443:443,8022:22`, порт ПК:порт роутера) для порта LAN «только этот
ПК»: сеть QEMU user-mode в /24 роутера с `restrict=on` и этими пробросами
со 127.0.0.1. Собственный DHCP-сервер QEMU в ней выключен (`dhcp=off`,
нужен libslirp ≥ 4.7): dnsmasq OpenWrt не раздаёт адреса на br-lan, пока
отвечает другой сервер.

## Пресеты плат

Пресет — INI-файл: `name`, `description`, `soc` (`mt7981` по умолчанию,
`mt7986`), `ram` (МБ), `nand-dir`, ключи
только для сборки пакета (`openwrt=` профиль OpenWrt, `openwrt-local=`
своя сборка, `openwrt-stock=` дампы стокового загрузчика) и параметры
машины. Их используют оба лаунчера; в Windows-лаунчере есть редактор
(Ethernet, подписи портов коммутатора, тип/размер RAM, загрузочный флеш и
второй флеш (SPI-NOR + SPI-NAND, `nor-nand`), USB, светодиоды (GPIO, PHY,
WS2812B, 74HC595, PWM), GPIO
кнопок с галкой «active high» — снята = active low, это умолчание и так
у большинства роутеров; при сохранении из редактора комментарии `;`
пропадают).

| Пресет | Ethernet | RAM | NAND | USB | Загрузчик |
|---|---|---|---|---|---|
| Cudy WR3000P v1 | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR4 512 МБ | 128 МБ (ESMT F50L1G41LB) | 2.0 | OpenWrt (U-Boot layout) |
| Cudy WR3000H v1 | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR3 256 МБ | 128 МБ | – | OpenWrt (U-Boot layout) |
| Cudy WR3000S v1, WR3000E v1 | 5×1G MT7531 (WAN = порт 0) | DDR3 256 МБ | 128 МБ | – | OpenWrt (U-Boot layout) |
| Cudy WBR3000UAX v1 | 5×1G MT7531 (WAN = порт 0) | DDR3 512 МБ | 128 МБ | 3.0 | OpenWrt (U-Boot layout) |
| Cudy WR3000U v1 | 5×1G MT7531 (WAN = порт 0) | DDR3 512 МБ | 256 МБ | 3.0 | стоковый (NMBM) |
| Cudy TR3000 v1 | 2.5G WAN RTL8221B на GMAC0 + 1G LAN встроенный PHY | DDR3 512 МБ | 128 МБ | 3.0 | OpenWrt (U-Boot layout) |
| Cudy TR3000 256MB v1 | то же, без коммутатора | DDR3 512 МБ | 256 МБ | 3.0 | стоковый (NMBM) |
| Bazis AX3000WM / WD-R3000N-G2A | 2.5G WAN MaxLinear GPY211C на GMAC0 + 1G LAN на встроенном PHY, без коммутатора | DDR3 256 МБ | 128 МБ, без bdinfo | 3.0 | OpenWrt |
| Cudy M3000 v1 / v2 (RTL8221B) | 2.5G WAN RTL8221B + 1G LAN встроенный PHY | DDR3 256 МБ | 128 МБ | – | OpenWrt (своя сборка, U-Boot layout) |
| Cudy M3000 v2 (YT8821) | 2.5G WAN Motorcomm YT8821 + 1G LAN встроенный PHY | DDR3 256 МБ | 128 МБ | – | OpenWrt (своя сборка, U-Boot layout) |
| Netis NX30 V2, NX31 | 1G WAN встроенный PHY + 3×1G LAN MT7531 | DDR3 256 МБ | 128 МБ | – | OpenWrt |
| Netis NX32U | 4×1G MT7531 (WAN = порт 0) | DDR3 256 МБ | 128 МБ | 3.0 | OpenWrt |
| Cudy WR3000 v1 | 4×1G MT7531 (WAN = порт 0) | DDR3 256 МБ | **SPI-NOR 16 МБ** (XM25QH128C) | – | стоковый |
| Xiaomi Mi Router AX3000T | 4×1G MT7531 (WAN = порт 0) | DDR3 256 МБ | 128 МБ, разметка Xiaomi | – | OpenWrt (U-Boot layout) |
| SNR-CPE-AX2 | 5×1G MT7531 (WAN = порт 0); GMAC1 связан с портом 5 коммутатора (WAN заводской прошивки SNR) | DDR3 256 МБ | 128 МБ, без bdinfo | – | OpenWrt; заводская прошивка SNR загружается с дампа всего NAND вашего роутера (не прилагается) |
| Huasifei WH3000 Pro NAND | 2.5G LAN RTL8221B + 1G WAN встроенный PHY, без коммутатора | DDR4 1 ГБ | 256 МБ, без bdinfo | 3.0 | стоковый (NMBM) |
| Huasifei WH3000R NAND | 1G WAN встроенный PHY + 3×1G LAN MT7531 | DDR3 512 МБ | 256 МБ, без bdinfo | 3.0 | стоковый (NMBM) |
| Keenetic KN-1012 | 1G WAN встроенный PHY + 4×1G LAN MT7531 (SFP не эмулируется) | DDR4 512 МБ | 256 МБ | 3.0 | стоковый (NMBM) |
| Xiaomi Redmi AX6000 (MT7986A) | 4×1G MT7531 (WAN = порт 4) | DDR4 512 МБ | 128 МБ | – | OpenWrt (U-Boot layout) |
| Netcore N60 (MT7986A) | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR3 256 МБ | 128 МБ | – | OpenWrt |
| Netcore N60 Pro (MT7986A) | 2.5G WAN GPY211 + 2.5G LAN GPY211 на порту 5 коммутатора + 3×1G MT7531 | DDR4 512 МБ | 128 МБ | 3.0 | OpenWrt |
| Bananapi BPi-R4 Lite (MT7987A) | 2.5G WAN внутренний PHY + 4×1G MT7531 (SFP не эмулируется); карта Wi-Fi 7 MT7992 на PCIe — для Wi-Fi поставьте `kmod-mt7996e kmod-mt7992-firmware` | DDR4 2 ГБ | 256 МБ на SPI2, FIP в UBI | 3.0 | OpenWrt |
| GL.iNet GL-MT6000 (MT7986A) | 2.5G WAN RTL8221B + 2.5G LAN RTL8221B на порту 5 коммутатора + 4×1G MT7531 | DDR4 1 ГБ | **eMMC** | 3.0 | OpenWrt |
| Huasifei WH3000 Pro eMMC | 2.5G LAN RTL8221B + 1G WAN на встроенном PHY, без коммутатора | DDR4 1 ГБ | **eMMC**, разметка производителя | 3.0 | OpenWrt (сборка для RAX3000M eMMC DDR4) |
| Routerich AX3000 | 3×1G LAN + WAN MT7531 (WAN = порт 4) | DDR3 256 МБ | 128 МБ, без bdinfo | 2.0 | OpenWrt (U-Boot layout) |
| Routerich AX3000 v1 | 1G WAN на встроенном PHY + 3×1G LAN MT7531 | DDR3 512 МБ | 128 МБ, без bdinfo | 3.0 | производителя (нужны свои дампы) |
| Routerich BE7200 (MT7987A) | 2.5G WAN внутренний PHY + 3×1G LAN **Airoha AN8855**; Wi-Fi 7 MT7992 по PCIe | DDR4 1 ГБ | **512 МБ** (W25N04KV) | 3.0 | OpenWrt |
| Asus ZenWiFi BT8 (MT7988D, Filogic 860) | 2.5G WAN внутренний PHY + 2×1G LAN встроенный коммутатор (lan3 на GMAC2 и Wi-Fi MT7996 не эмулируются) | DDR4 1 ГБ | 128 МБ, FIP и Factory в UBI | 3.0 | OpenWrt (U-Boot layout) |
| TP-Link TL-7DR7230 v1 (MT7988D, Filogic 860) | 2.5G WAN внутренний PHY + 4×1G LAN встроенный коммутатор | DDR4 512 МБ | 128 МБ, разметка TP-Link | – | OpenWrt **snapshot** (без LuCI) |
| Bananapi BPi-R4 (MT7988A, Filogic 880) | WAN + 3×1G LAN встроенный коммутатор (SFP+ не эмулируются) | DDR4 2 ГБ (на плате 4 ГБ) | **eMMC** (разметка `bpi-r4`) | 3.0 | OpenWrt |
| Teltonika RUTC50 | 1G WAN встроенный PHY + 4×1G LAN MT7531 (мобильный модем не эмулируется); светодиоды на **74HC595** | DDR3 512 МБ | **SPI-NOR 16 МБ (загрузка) + SPI-NAND 512 МБ** (rutos-a / rutos-b) | 2.0 | BL2 OpenWrt RFB + свой U-Boot ([`tools/uboot-rutc50`](tools/uboot-rutc50)) |
| OpenWrt One | 2.5G WAN **Airoha EN8811H** + 1G LAN встроенный PHY | DDR4 1 ГБ | **SPI-NAND 256 МБ (загрузка) + SPI-NOR 16 МБ** (recovery) | 2.0 | OpenWrt |
| Zbtlink ZBT-WG3526 (16M) (**MT7621A**, MIPS) | 5×1G MT7530 (WAN = порт 4); Wi-Fi MT7603E + MT7612E на PCIe («радиомолчащие») | DDR3 512 МБ (доступно 448 МБ) | **SPI-NOR 16 МБ** | 3.0 | OpenWrt |
| Xiaomi Mi Router 3G (стоковый U-Boot) (**MT7621A**, MIPS) | 1G WAN + 2×1G LAN MT7530; Wi-Fi MT7603E + MT7612E на PCIe («радиомолчащие») | DDR3 256 МБ | **параллельный NAND 128 МБ** (разметка Xiaomi) | 3.0 | стоковый U-Boot 1.1.3 Xiaomi + OpenWrt |
| Xiaomi Mi Router 3G (Breed) (**MT7621A**, MIPS) | так же | DDR3 256 МБ | **параллельный NAND 128 МБ** (разметка Xiaomi) | 3.0 | Breed 1.2 + OpenWrt |
| Xiaomi Mi Router 4 (стоковый U-Boot) (**MT7621A**, MIPS) | 1G WAN (PHY 4 MT7530 напрямую на GMAC1) + 2×1G LAN MT7530; Wi-Fi MT7603E + MT7612E на PCIe («радиомолчащие») | DDR3 128 МБ | **параллельный NAND 128 МБ** (разметка Xiaomi) | – | стоковый U-Boot 1.1.3 Xiaomi + OpenWrt |
| Cudy WR1300S (**MT7621A**, MIPS) | 1G WAN (PHY 0 MT7530 напрямую на GMAC1) + 4×1G LAN MT7530; Wi-Fi MT7603E + MT7663E (MT7613BE) на PCIe («радиомолчащие») | DDR3 128 МБ | **SPI-NOR 16 МБ** | 3.0 | стоковый U-Boot 1.1.3 Cudy + OpenWrt 25.12.5 (сторонняя сборка, платы ещё нет в OpenWrt) |
| Cudy M1300 v2 (**MT7621A**, MIPS) | 1G WAN (PHY 4 MT7530 напрямую на GMAC1) + 1G LAN порт 3 MT7530; Wi-Fi MT7603E + MT7663E (MT7613BE) на PCIe («радиомолчащие») | DDR3 128 МБ | **SPI-NOR 16 МБ** | – | стоковый U-Boot 1.1.3 Cudy + OpenWrt; стоковая прошивка Cudy (LEDE, ядро 4.4) — из дампа флеша вашего роутера |

У плат Netis нет раздела bdinfo (FIP с 0x380000, ubi с 0x580000; MAC — в
Factory): в пресетах стоит `openwrt-no-bdinfo=1`. У плат на MT7986 та же
разметка. Другие разметки задаются ключом `openwrt-parts=метка:размер,...`
(`tools/prepare-nand.sh --parts`), например у Xiaomi AX3000T: BL2, Nvram,
Bdata, Factory, FIP, crash, crash_log, ubi с 0x600000, KF. Его прошивка
называет порты LAN lan2..lan4; `ports` в пресете — имена подключений
эмулятора, поэтому LAN лаунчера (`lan1`) — это порт 1 коммутатора.

## MT7986 (Filogic 830)

`mt7986-router` — та же машина с отличиями MT7986: четыре ядра
Cortex-A53, HW_CODE 0x7986, pinctrl по 0x1001f000 (101 GPIO), eFuse по
0x11d00000, два контроллера SPI, нет встроенного GbE PHY, прерывание
MT7531 на EINT 66. Его BL2 (готовая библиотека DRAM от MediaTek, DDR3 и
DDR4) калибруется на той же модели контроллера DRAM и определяет размер
памяти тестовым агентом контроллера (запись за пределами `-m`
заворачивается, как на настоящем чипе). Драйвер Wi-Fi видит один a-die
MT7976 (2,4 + 5 ГГц); EEPROM по умолчанию для MT7986 в OpenWrt нет, поэтому
`tools/prepare-nand.sh --soc mt7986` записывает минимальный в пустой
Factory (с дампом Factory настоящей платы берётся её калибровка).

```bash
tools/prepare-nand.sh --soc mt7986 --no-bdinfo netcore_n60-pro 25.12.5 nand-n60-pro
./emulator.sh -P netcore-n60-pro
```

## MT7987 (Filogic 850)

`mt7987-router`: UART по 0x11000000, SPI0..2 по 0x11007800 / 0x11008800 /
0x11009800, TOP_MISC по 0x10021000, eFuse по 0x11d30000, frame engine
NETSYS v3 (PDMA по 0x6800), внутренний 2.5G PHY (`gmac1=i2p5ge`, MDIO 15;
загрузка прошивки и линк), датчик LVTS, регистр бутстрапа (тип DDR для BL2
«comb», загрузочный носитель — по нему U-Boot выбирает rootdisk). BL2 для
MT7987 калибрует DRAM на той же модели контроллера. Параметры для его
плат: `nand-spi=2` (SPI-NAND на SPI2), `switch-irq-gpio=41`, `-m 2G`.

Wi-Fi у MT7987 на PCIe: в машине есть контроллер Gen3 порта 0
(`mtk_pcie.c`: линк, TLP конфигурации, INTx, приём MSI; корневой порт
— стандартный из QEMU), а с `pcie-wifi=mt7992` в слоте стоит карта
MT7992, Wi-Fi 7 (`mt7992_wifi.c`, «немая», как встроенный Wi-Fi других
SoC: прошивка загружается и отвечает, оба диапазона поднимаются, hostapd
работает, в эфир ничего не уходит). В официальном образе драйвера нет;
поставьте его один раз (WAN должен видеть интернет, пакеты сохранятся в
overlay):

```bash
apk update && apk add kmod-mt7996e kmod-mt7992-firmware
reboot
```

Затем включите в LuCI `radio0` / `radio1` и их сети (в OpenWrt Wi-Fi по
умолчанию выключен).

```bash
tools/prepare-nand.sh --soc mt7987 --flash-mb 256 --ubi-fip bananapi_bpi-r4-lite 25.12.5
./emulator.sh -P bananapi-bpi-r4-lite
```

Платы с eMMC (GL.iNet GL-MT6000) — `flash=emmc`: модель контроллера MSDC
обслуживает TF-A и U-Boot (PIO) и Linux (DMA по дескрипторам); BL2
загружается из загрузочного раздела boot0 eMMC («EMMC_BOOT»), FIP, ядро и
rootfs — разделы GPT. `tools/prepare-nand.sh --soc mt7986 --emmc
glinet_gl-mt6000 25.12.5` собирает образ через
[`tools/mkemmc.py`](tools/mkemmc.py) (разреженный, 1 ГБ).

У Huasifei WH3000 Pro eMMC (MT7981B) разметка GPT от производителя
(u-boot-env, factory, fip, config, kernel, rootfs; `--emmc-layout
wh3000-pro`), а OpenWrt собирает для неё только прошивку (`sysupgrade.bin` —
tar с FIT ядра и squashfs корня), без загрузчика. Пресет (`openwrt-uboot=`,
`--uboot`) берёт BL2 и FIP OpenWrt от CMCC RAX3000M eMMC DDR4 (тот же SoC,
носитель загрузки и тип памяти) и записывает окружение U-Boot, которое
загружает FIT из раздела `kernel`; `root=PARTLABEL=rootfs` ядро берёт из
своего дерева устройств, как и со стоковым загрузчиком. С дампами стоковых
BL2 (`*boot0*.bin`) и FIP (`*fip*.bin`) ключ `--stock ПАПКА` оставляет
стоковый загрузчик:

```bash
tools/prepare-nand.sh --emmc --emmc-layout wh3000-pro --uboot cmcc_rax3000m-emmc-ddr4 huasifei_wh3000-pro-emmc 25.12.5 emmc-wh3000-pro
./emulator.sh -P huasifei-wh3000-pro-emmc
```

## MT7988 (Filogic 880 / 860)

`mt7988-router`: MT7988A (Filogic 880, 4 ядра) и MT7988D (Filogic 860:
тот же кристалл, DT платы выключает cpu@3). Четыре ядра Cortex-A73 (TF-A
для MT7988 содержит код только для Cortex-A73; `cortex-a73` в QEMU — модель
A72 с ID A73), MCUCFG на 0x100e0000, UART на 0x11000000, SPI0..2 на
0x11007000 / 8000 / 9000, eFuse на 0x11f50000, NETSYS v3 (PDMA на 0x6800),
внутренний 2.5G PHY (`gmac1=i2p5ge`, MDIO 15, прошивка MT7988),
**встроенный коммутатор** (`gmac0=mt7988-switch`: 4 порта, их GbE PHY с
калибровкой из eFuse и регистрами LED), xHCI ssusb1 (USB 3.0), MSDC
(eMMC), PCIe (`pcie-wifi=`), два контроллера LVTS и регистр boot strap, по
которому U-Boot выбирает `/chosen/rootdisk-<носитель>`. Официальный BL2 (и
комбинированные DDR3/DDR4) калибрует DRAM на той же модели контроллера.

Не эмулируются: GMAC2 / порты 10G USXGMII (SFP+, lan3 у BT8), контроллер
SNFI NAND (BL2 `snand`: BPi-R4 здесь грузится с eMMC), PMIC RT5190A
(cpufreq остаётся на частоте загрузки), карты Wi-Fi MT7996.

```bash
tools/prepare-nand.sh --soc mt7988 --ubi-fip --ubi-factory 0x200000 asus_zenwifi-bt8 25.12.5
./emulator.sh -P asus-zenwifi-bt8
tools/prepare-nand.sh --soc mt7988 --emmc --emmc-layout bpi-r4 bananapi_bpi-r4 25.12.5
./emulator.sh -P bananapi-bpi-r4
```

Asus ZenWiFi BT8 хранит MAC-адреса и EEPROM Wi-Fi в UBI-томе `factory`
(`--ubi-factory РАЗМЕР`, ключ пресета `openwrt-ubi-factory=`). Образ eMMC
BPi-R4 повторяет `emmc-gpt.bin` из OpenWrt (ubootenv, factory, fip,
recovery, production; FIT sysupgrade пишется в `production`). TP-Link
TL-7DR7230 v1 есть только в снапшоте OpenWrt: в пресете
`openwrt-version=snapshot` (в снапшот-образах нет LuCI, SSH работает).

## SPI-NOR + SPI-NAND (Teltonika RUTC50, OpenWrt One)

С `nor-nand=on` у платы оба флеша: SPI-NOR на SPI2 и SPI-NAND на SPI0
(`nand-spi`). В папке флеша лежат файлы разделов NOR, в её подпапке `nand`
— файлы NAND (те же имена `*mtdN*`, у каждого флеша свои); `flash=nor` или
`flash=nand` задаёт, с какого флеша грузится BootROM, — это переключатель
загрузки OpenWrt One.

```
nor-rutc50/mt7981.mtd0.BL2.bin … mt7981.mtd8.recovery.bin    SPI-NOR 16 МБ
nor-rutc50/nand/mt7981.mtd0.rutos-a.bin, mt7981.mtd1.rutos-b.bin   SPI-NAND 512 МБ
```

- **Teltonika RUTC50** грузится с NOR (BL2, u-boot-env, factory, config с
  MAC-адресами, FIP, bootconfig-a/b, event-log, recovery); система — UBI
  (kernel, rootfs, rootfs_data) в разделе NAND `rutos-a`. Своего
  загрузчика у OpenWrt для неё нет, а сборки U-Boot OpenWrt для SPI-NOR
  MT7981 не умеют читать UBI (RFB) или грузят только NOR (OpenWrt One),
  поэтому пресет использует BL2 OpenWrt MT7981 RFB для SPI-NOR и U-Boot,
  собранный [`tools/uboot-rutc50/build.sh`](tools/uboot-rutc50/build.sh)
  (исходники и патчи U-Boot OpenWrt, конфигурация RFB SPI-NOR с UBI,
  разметка флеша RUTC50; готовый FIP лежит в той же папке). Его окружение
  грузит `rutos-a` с `ubi.mtd=rutos-a`, как и загрузчик Teltonika, поэтому
  скрипты sysupgrade и bootcount OpenWrt работают. Светодиоды мобильной
  сети, SIM и сигнала висят на 74HC595 на SPI1 (`hc595:1:N`).
- **OpenWrt One** — официальные `snand-factory.bin` (BL2 + UBI с FIP,
  ubootenv, recovery, production) и `nor-factory.bin` (BL2 recovery,
  Factory с EEPROM Wi-Fi, FIP и система recovery). Пресет грузится с NAND;
  `-o flash=nor` — переключатель в положении NOR (U-Boot и система
  recovery). 2.5G PHY порта WAN — Airoha EN8811H (`gmac0=en8811h`): Linux
  загружает прошивку его MCU по MDIO, после чего линк поднимается на
  2,5 Гбит/с.

```bash
tools/prepare-nand.sh --nor-nand rutc50 teltonika_rutc50 25.12.5
./emulator.sh -P teltonika-rutc50
tools/prepare-nand.sh --nor-nand openwrt-one openwrt_one 25.12.5
./emulator.sh -P openwrt-one            # -o flash=nor: recovery с NOR
```

## MT7621 (MIPS)

Платы на MediaTek MT7621A работают на втором бинарнике QEMU,
`qemu-system-mipsel`, машина `mt7621-router` (ключ пресета `soc=mt7621`;
`emulator.sh`, лаунчер и `tests/quick.py` выбирают бинарник сами).
Цепочка загрузки настоящая: CPU стартует из SPI-NOR, отображённого на
`0x1fc00000`; TPL из U-Boot блокирует L2-кэш и загружает в него SPL, SPL
запускает blob инициализации DDR от MediaTek в SRAM Frame Engine, дальше
U-Boot и OpenWrt; NAND-платы грузятся через BootROM для NAND (stage1 с
инициализацией DDR — в заблокированный L2, затем загрузчик). Эмулируется: MIPS 1004Kc, 2 ядра × 2 VPE (MT ASE: 4 CPU в Linux,
chip ID MT7621A; `-smp 2` — одно ядро, MT7621S; `-smp 1`), CM/GIC/CPC
(ядра включает CPC, VPE — код MT ASE; таймер CP0 первого VPE —
на его IP7, остальных — через GIC), DRAM 128/256/512 МБ (выше 448 МБ — по
`0x20000000`, как у SoC), SPI-контроллер + SPI-NOR, UART, Frame Engine
(QDMA/PDMA, NETSYS v1) с коммутатором MT7530 (MCM), xHCI (USB 3.0 + 2.0),
GPIO с кнопками и светодиодами, PCIe-хост (три корневых порта,
`pcie0..2=КАРТА`) с «радиомолчащими» Wi-Fi-картами для неизменённых
драйверов mt76: `mt7603e` (MT7603E, 2,4 ГГц), `mt7612e` (MT7612E, 5 ГГц) и
`mt7663e` (MT7663E / MT7613BE, 5 ГГц, драйвер mt7615e: ROM-патч и прошивка
N9) загружают прошивку, hostapd работает, в эфир ничего не уходит; карта без
EEPROM во флеше получает минимальный из eFuse с MAC 80:af:ca:11:22:35 +
номер слота; параллельный NAND (NFI + ECC, PIO, `flash=nand`,
`nand-id=`); программный тестовый движок DRAMC, так что вендорский stage1,
который сам калибрует DDR (стоковый U-Boot Xiaomi), находит окна;
`poweroff=stop|halt` (`poweroff` / `halt` в Linux закрывают эмулятор или
он остаётся остановленным, как сам чип, который не умеет выключать
питание); кэш
данных L1 с обратной записью — в части некэшируемых записей (`dcache=on`,
по умолчанию: Breed выходит некэшируемым memset за конец буфера в заголовок
malloc и полагается на грязную копию в кэше; включён от сброса до первого
пользовательского кода ОС, так что OpenWrt работает на полной скорости).
Пока нет: SD-карты.

```bash
tools/prepare-nand.sh --soc mt7621 zbtlink_zbt-wg3526-16m 25.12.5   # nor-zbt-wg3526-16m
./emulator.sh -P zbtlink-zbt-wg3526-16m
```

В папке флеша: `mt7621.mtd0.u-boot.bin` (сборка U-Boot OpenWrt
`u-boot-mt7621_zbtlink_zbt-wg3526-16m/u-boot-mt7621.bin`), `u-boot-env`,
`factory` (MAC 80:af:ca:11:22:33 по 0xe000: LAN …:33, WAN …:34) и
`firmware` (sysupgrade.bin). В образе OpenWrt нет `kmod-usb-storage`: для
USB-флешки его нужно поставить (`apk add kmod-usb-storage`).

**Xiaomi Mi Router 3G** (два пресета, по загрузчику на каждый): своего
загрузчика у OpenWrt для него нет, поэтому папка флеша собирается из дампа
раздела `Bootloader` роутера (стоковый U-Boot или Breed) и, если есть, его
`factory` (калибровка Wi-Fi; MAC заменяются на 80:af:ca:11:22:33 и далее:
WAN …:33, LAN …:34, Wi-Fi …:35 / …:36):

```bash
# DIR: *mtd0*.bin или *Bootloader*.bin (+ *factory*.bin)
tools/prepare-nand.sh --soc mt7621 --stock DIR xiaomi_mi-router-3g 25.12.5 nand-mi-router-3g-uboot
./emulator.sh -P xiaomi-mi-router-3g-uboot     # или -breed
```

**Xiaomi Mi Router 4** собирается так же (`xiaomi_mi-router-4`, папка
`nand-mi-router-4`, пресет `xiaomi-mi-router-4`); его WAN — PHY 4
коммутатора MT7530, подключённый через порт 5 прямо к GMAC1 (PHY muxing),
эмулятор следует регистру MTRAP коммутатора. Нужны только `Bootloader` и
`factory`: в `Config` (переменные стокового U-Boot) и `Bdata` лежат
серийный номер, MAC и пароли роутера, их не кладите.

**Cudy WR1300S** (SPI-NOR, стоковый U-Boot 1.1.3 Cudy): в OpenWrt его ещё
нет ([PR #22365](https://github.com/openwrt/openwrt/pull/22365)), поэтому в
папку идёт сборка OpenWrt 25.12.5 под него. Стоковый U-Boot читает ядро
через окно отображения флеша на 0x1c000000 и работает с контроллером SPI с
автоматическим chip select — оба эмулируются. В его `bdinfo` лежат данные
платы (зашифрованные) и MAC: раздел генерируется (MAC 80:af:ca:11:22:33),
с роутера берутся только `u-boot` и `factory`:

```bash
# DIR: *mtd0*.bin или *u-boot*.bin, *factory*.bin, *sysupgrade.bin
tools/prepare-nand.sh --soc mt7621 --nor --stock DIR cudy_wr1300s 25.12.5 nor-wr1300s
./emulator.sh -P cudy-wr1300s
```

**Cudy M1300 v2** есть в OpenWrt (`cudy_m1300-v2`, папка `nor-m1300-v2`) и
собирается так же, из его `u-boot` и `factory`. В стоковой прошивке оба
порта автоопределяемые; OpenWrt делает LAN порт 3 коммутатора, а WAN — PHY 4
(через порт 5 на GMAC1). Стоковая прошивка Cudy (LEDE, ядро 4.4,
вендорские драйверы Wi-Fi MediaTek) тоже работает: разрежьте дамп всего
флеша 16 МБ по 0x30000 / 0x40000 / 0x50000 / 0xff0000 на
`mt7621.mtd0.u-boot.bin` ... `mt7621.mtd4.bdinfo.bin` в одной папке и
передайте её через `-n`. Её адрес LAN — 192.168.10.1
(`-o lan-ip=192.168.10.1` с `-l user`), shell в консоли нет. В пресете —
микросхема NOR платы (`nor-id=204018`, XMC XM25QH128D). Стоковая прошивка
сверяет уникальный ID микросхемы с `bdinfo`: без ID вашего роутера пароль
администратора нельзя ни задать, ни проверить («Invalid password!»).
Передайте его в `nor-uid` — 16 цифр после `-` из
`cat /proc/sys/dev/flash_uuid` на роутере:
`-o lan-ip=192.168.10.1,nor-uid=0123456789ABCDEF`.

В папке (`tools/mknand.py mt7621-nand`) — стоковые разделы, `kernel1.bin`
OpenWrt в `kernel` (0x600000, «System 2») и `rootfs0.bin` в `ubi`;
`kernel_stock` (System 1, ядро стоковой прошивки) остаётся стёртым, и
стоковый U-Boot переходит на System 2, как на роутере с OpenWrt; Breed
грузит это же ядро. U-Boot MediaTek хранит фабричную таблицу сбойных
блоков в последних 4 блоках, вне всех разделов (пишется при первой
загрузке, поэтому в дампах разделов её нет; без неё стоковый U-Boot
сканирует занятую флешку на метки сбойных блоков, принимает данные за
метки и пропускает ядро): если эти блоки не покрыты ни одним дампом,
эмулятор сам кладёт туда таблицу «все блоки хорошие». У любого дампа `X` может быть `X.oob` (его spare, 64 байта на
страницу).

## Что эмулируется

Все модели устройств — в `hw/arm/filogic/` дерева QEMU
(патчи: [`qemu-patches/`](qemu-patches/), база QEMU v10.1.0).

| Блок | Модель | Примечания |
|---|---|---|
| CPU, GIC | 2× (MT7986, MT7987: 4×) Cortex-A53, MT7988: 4× Cortex-A73 (`cortex-a73` в QEMU: модель A72 с ID A73 и CPUACTLR_EL1) (EL3/EL2), GICv3, таймер 13 МГц | остальные ядра запускает BL31 через SPMC в TOPMISC; PSCI — от BL31 |
| BootROM | высокоуровневая эмуляция (`filogic_router.c`) | разбирает заголовок `SPINAND!` и GFH `FILE_INFO`, грузит BL2 в L2 SRAM, переходит на EL3 |
| Контроллер DRAM | `mtk_sysctl.c` + сгенерированная таблица статусов | broadcast, ответы RTSWCMD/MRW, jitter meter, lead/lag DQS gating, «глаз» приёма RX: бинарная калибровка DRAM от MediaTek находит настоящие окна, лог BL2 чистый (DDR3 и DDR4); биты DDR3EN/DDR4EN регистра `DDRCOMMON0` сверяются с `ddr=` |
| Клоки, питание, прочее | `mtk_sysctl.c` | разреженный регистровый файл + особые случаи (частотомер, включение CPU, TRNG v2, калибровочные данные eFuse, IPPC, термодатчик ≈45 °C, ID EIP-97, биты сброса WED) |
| APXGPT | `mtk_sysctl.c` | 8 таймеров общего назначения (нужны стоковым BL2) |
| TOPRGU | `mtk_toprgu.c` | watchdog с реальным таймаутом, программный сброс (reboot), статус сброса сохраняется |
| UART ×3 | 16550 из QEMU + регистры MTK | |
| SPI (IPM) | `mtk_spim.c` | FIFO + DMA, полудуплексный режим spi-mem (TF-A/U-Boot/Linux) |
| SPI-NAND | `spinand.c` | W25N01GV (2048+64) / W25N02KV (2048+128) / W25N04KV (512 МБ, 2048+128, две плоскости), on-die ECC, ONFI parameter page, страница уникального ID; хранилище — папка с дампами разделов (см. ниже) или один raw-образ с OOB |
| eMMC | `mtk_msdc.c` + eMMC из QEMU | контроллер MSDC: команды, авто CMD23/CMD12, PIO через FIFO (TF-A, U-Boot), DMA по дескрипторам GPD/BD (Linux); карта с boot0/boot1 и пользовательской областью; BootROM загружает образы `EMMC_BOOT` из boot0 |
| SPI-NOR | `spinor.c` | 3-байтовая адресация (до 16 МБ), задаваемый JEDEC ID, чтение 1-1-1/1-1-2/1-1-4/1-2-2/1-4-4, запись страниц, стирание 4K/32K/64K/всего чипа, регистры статуса с QE; BootROM загружает с него образы `SF_BOOT`; хранилище — такая же папка; с `nor-nand=on` — вместе с SPI-NAND |
| Ethernet | `mtk_eth.c` | frame engine: QDMA TX (Linux), PDMA RX, PDMA v2 (U-Boot); TSO и offload контрольных сумм; 2× LynxI SGMII PCS; любая комбинация коммутатора и PHY на двух GMAC; маршрутизация прерываний mainline Linux и SDK-драйвера MediaTek (заводские прошивки: группы `FE_INT_GRP`, PDMA RX на линиях pdma, GIC 189..192) |
| Коммутатор | `mtk_eth.c` | MT7531: страничный доступ по MDIO, косвенный доступ к PHY, special tag MTK (DSA), FDB с обучением, port matrix, таблица VLAN (SDK-драйвер коммутатора MediaTek), порт 5 к PHY или к GMAC1, IRQ линка → EINT 38. Airoha AN8855: 32-битная шина регистров через страницу 4 MDIO, внутренние PHY на MDIO 1..5, регистры транслируются в модель MT7531 (CPU-порт 5). Встроенный коммутатор MT7988: модель MT7531 по MMIO 0x15020000, GbE PHY SoC (ID MT7988, калибровка из eFuse) |
| PHY | `mtk_eth.c` | 5× GPHY MT7531; RTL8221B-VB-CG (C45, термодатчик); Motorcomm YT8821 (расширенные регистры, пространства UTP/SerDes, статус 2.5G); MaxLinear GPY211C (C45, версия прошивки, mailbox, термодатчик); Airoha EN8811H (окно buckpbus на странице 4 MDIO, загрузка прошивки MCU, статус 2.5G, регистры LED); встроенный GbE PHY MT7981 (с калибровкой); PHY на порту 5 MT7531; необязательные GPIO аппаратного сброса |
| GPIO / EINT | `mtk_pinctrl.c` | кнопки (reset/WPS, `reset-hold-ms` для нажатия на время), лог светодиодов, уровни выводов для других устройств |
| Светодиоды | `filogic_leds.c`, `ws2812b.c`, `hc595.c` | светодиоды передней панели из пресета: GPIO, регистры LED у PHY (RTL8221B, GPY211, EN8811H, GbE / 2.5G PHY MediaTek, в том числе портов коммутатора), WS2812B на SPI, сдвиговые регистры 74HC595 на SPI (защёлкиваются по CS), каналы PWM; распознавание мигания; `led-state` |
| USB | xHCI из QEMU + IPPC MTK | разъём USB 2.0 / 3.0 |
| Wi-Fi | `mtk_wmac.c` | кольца WFDMA + эмуляция командного интерфейса прошивок WM/WA: прошивка грузится, оба диапазона поднимаются, hostapd работает, в эфире ничего нет (сканирование пустое) |
| PCIe (MT7987, MT7988) | `mtk_pcie.c` | хост MediaTek Gen3: линк, TLP конфигурации через CFGNUM, INTx, приём MSI; корневой порт из QEMU |
| Карта Wi-Fi 7 (MT7987) | `mt7992_wifi.c` | MT7992 для mt7996e: окна BAR0, кольца WFDMA, загрузка патча и прошивок, ответы на UNI-команды, события TX free; радио «молчит» |
| Крипто (EIP-97) | только ID | драйвер safexcel видит «нет packet engine» и отключается; работает программная криптография |
| WED | `mtk_wmac.c`, `mtk_wo.c`, биты сброса | для вендорского Wi-Fi-драйвера MediaTek (заводские прошивки: mt_wifi + mtk_warp): включение приёма Wi-Fi и прерывания через WED, а процессор offload WO — заглушка сообщений (почтовый ящик CCIF и кольца; на каждый запрос есть ответ, heartbeat, «загружен»), чтобы драйвер не ждал по 3 с на сообщение. Пути данных offload нет; mt7915e в OpenWrt использует WED только с `wed_enable=1` (с заглушкой WO не проверялось) |

Новый netdev `pcap` (`net/pcap.c`, libpcap / Npcap загружается при запуске)
подключает порт к сетевой карте хоста как «сетевой мост» — им пользуется
Windows-лаунчер.

## Флеш (папка NAND)

Папка NAND содержит дампы разделов без OOB. **Каждый файл, в имени которого
есть `mtdN`, становится разделом N**; файлы склеиваются по порядку mtd0,
mtd1, … в полный образ, например `mt7981.mtd0.BL2.bin`,
`mt7981.mtd1.u-boot-env.bin`, `mt7981.mtd2.Factory.bin`,
`mt7981.mtd3.bdinfo.bin`, `mt7981.mtd4.FIP.bin`, `mt7981.mtd5.ubi.bin`.

Всё, что роутер пишет во флеш (настройки, sysupgrade, env U-Boot),
записывается обратно в эти файлы. Дампы с настоящего роутера
(`cat /dev/mtdN > имя.mtdN.метка.bin`) подходят напрямую. При `flash=nor`
папка — это содержимое SPI-NOR (например, BL2, u-boot-env, Factory,
bdinfo, FIP, firmware); с `nor-nand=on` файлы NAND лежат в её подпапке
`nand`. Если файлы меньше флеша (например, дамп 128 МБ
запущен с `nand=256`), при первом запуске последний файл дополняется
стёртыми байтами (`0xff`) до размера флеша, чтобы то, что роутер туда
запишет, сохранялось.

- [`tools/prepare-nand.sh`](tools/prepare-nand.sh) `[--stock ПАПКА [--nor] | --local ПАПКА] [--flash-mb N] [--no-bdinfo] ПРОФИЛЬ [ВЕРСИЯ] [ПАПКА_NAND]` — собирает папку NAND для профиля устройства OpenWrt: скачивает официальные образы с U-Boot OpenWrt (`ПРОФИЛЬ-ubootmod-*` или `ПРОФИЛЬ-*`, с проверкой sha256), или берёт свои сборки (`--local`), или оставляет стоковые BL2/FIP (`--stock`) с OpenWrt `sysupgrade.bin` в стоковой разметке; `--no-bdinfo` — для плат без раздела bdinfo, `--nor` — для плат с SPI-NOR (стоковые BL2/FIP + OpenWrt `sysupgrade.bin` в разделе `firmware`), `--soc mt7986` — для плат на MT7986 (файлы разделов `mt7986.mtdN.*`, минимальный EEPROM Wi-Fi в пустом Factory: ID чипа, MAC, 4 тракта, целевая мощность 20 дБм), `--ubi-fip` — для BL2 «spim-nand-ubi» (BL2 с 0, UBI с 0x200000, FIP — том `fip`), `--ubi-factory РАЗМЕР` (Factory как UBI-том `factory`, Asus BT8), `--emmc [--emmc-layout L]` (образ eMMC, см. [MT7988](#mt7988-filogic-880--860)), `--nor-nand rutc50|openwrt-one` (папка SPI-NOR + SPI-NAND, см. [выше](#spi-nor--spi-nand-teltonika-rutc50-openwrt-one)). Factory/bdinfo берутся из `factory/`.
- [`tools/mknand.py`](tools/mknand.py) — создание и правка образов: `create` (BL2, FIP, Factory, bdinfo, UBI из `.itb` или `sysupgrade.bin`), `write --part fip`, `read`, `split`, `join`, `--flash-mb 256`, `--no-bdinfo`, `nor` (папка SPI-NOR: разметка `--parts`, образ всего чипа `--raw`, `--mac-part`, окружение U-Boot `--env`; с `--nand-parts`, `--nand-file`, `--nand-sysupgrade` — ещё и подпапка `nand`).
- [`tools/kernel-dts.sh`](tools/kernel-dts.sh) — DTS MediaTek из ядра с DTS-частями патчей OpenWrt (`work/kdts`) для плат, чей DTS лежит в дереве ядра (BPi-R4); оттуда их читает [`tools/dts-leds.py`](tools/dts-leds.py).
- [`tools/mkemmc.py`](tools/mkemmc.py) — образ eMMC (`flash=emmc`): BL2 в boot0, GPT с разделами u-boot-env, factory, fip, kernel, rootfs (разметка GL-MT6000, `--layout wh3000-pro` или `--layout bpi-r4` с `--fit` / `--recovery` — FIT в production / recovery), OpenWrt `squashfs-factory.bin` в kernel + rootfs или `--sysupgrade` (tar sysupgrade), `--boot-env` (окружение U-Boot OpenWrt, загружающее раздел `kernel`).

## Запуск

Linux: [`emulator.sh`](emulator.sh) — `-P ПРЕСЕТ` (`-P list`), `-o ПАРАМЕТРЫ`
(переопределить параметры машины, `ram=`), `-n ПАПКА_NAND`,
`-w bridge|user|offline|none` (`offline` — WAN user-mode с `restrict=on`:
DHCP работает, наружу ничего не уходит), `-l isolated|nic|user|none` (`user` — только этот
ПК, пробросы из пресета), `-p "1 3"` (порты LAN),
`-u ПАПКА` (USB-флешка из папки, FAT16), `-L ПАПКА` (логи консоли),
`-g` (лог GPIO и светодиодов), `-R` (включение с зажатым reset на 10 с → TFTP recovery),
`-d` (лог неэмулированных регистров), `-S СОКЕТ` (без терминала: консоль
на unix-сокете, для скриптов и CI; подключиться —
`socat -,raw,echo=0,escape=0x1d UNIX-CONNECT:СОКЕТ`). Переменные окружения `WAN_EXTRA` /
`LAN_EXTRA` дописываются к user-mode netdev, например
`WAN_EXTRA=",guestfwd=tcp:10.0.2.100:80-cmd:nc 127.0.0.1 8000"` даёт роутеру
локальный тестовый сервер даже при `-w offline`. Пример без терминала и без
root (веб-интерфейс роутера — http://127.0.0.1:8080/):

```bash
./emulator.sh -P cudy-wr3000p-v1 -w offline -l user -S work/console.sock &
socat -,raw,echo=0,escape=0x1d UNIX-CONNECT:work/console.sock   # консоль, Ctrl-] — отключиться
echo quit | socat - UNIX-CONNECT:work/monitor.sock    # остановить
```
Сеть хоста: [`tools/host-bridge.sh`](tools/host-bridge.sh) (`br0` с сетевой
картой для WAN, изолированный `br-wrlan` для LAN — LAN в реальной сети
выставил бы туда DHCP/RA роутера).

Windows: `emulator.exe` — пресет платы (с редактором: New / Edit / Save /
Save as / Delete), папка NAND, WAN/LAN (NAT, «только этот ПК» с пробросом
портов на LuCI/SSH или мост на сетевую карту через Npcap; если Npcap не
даёт открыть карту без прав администратора, предлагает перезапуск от имени
администратора), USB-папка,
папка логов, кнопки Reset/WPS, «Power + Reset: 10 s» (TFTP recovery),
выключение по `poweroff`, встроенный терминал (VT100, по умолчанию 80×24
как в PuTTY, размер прилипает к целым символам, выделение = копирование,
правая кнопка = вставка, Ctrl+Shift+R подгоняет размер консоли роутера
под окно), язык интерфейса переключается на лету
([`languages/*.ini`](languages/): English, Русский; новый язык — копия
`en.ini`; [`tools/gen-lang-en.py`](tools/gen-lang-en.py) пересобирает
`en.ini` из исходников, `--check` показывает непереведённые ключи).

## USB Wi-Fi-донгл

Эмулированные радио Wi-Fi ничего не излучают. Для настоящего Wi-Fi можно
пробросить в роутер USB Wi-Fi-донгл этого ПК (`usb-host=VID:PID`, несколько
через `;`; Windows-лаунчер: «USB-устройства» → «Выбрать...»); OpenWrt управляет им своим драйвером
как ещё одним радио (`radio2`, путь `…/usb1/1-2/1-2:1.0`) рядом с
молчащими встроенными и запускает на нём hostapd, как на настоящем железе.

1. Linux: `./emulator.sh … -o usb-host=148f:5370` (QEMU, собранный с
   libusb: `libusb-1.0-0-dev`). QEMU открывает `/dev/bus/usb/…`, нужны
   права на запись (иначе emulator.sh подскажет правило udev); драйвер
   хоста отпускает донгл, пока им пользуется роутер.
   Windows: установите [UsbDk](https://github.com/daynix/UsbDk/releases)
   (донгл остаётся на своём драйвере Windows и забирается только на время
   работы роутера) или поставьте на него драйвер WinUSB через Zadig.
2. В OpenWrt один раз поставьте драйвер донгла (нужен Интернет на WAN),
   например Ralink RT5370 / RT3070: `apk add kmod-rt2800-usb
   rt2800-usb-firmware`; MediaTek MT7601U: `kmod-mt7601u`; MT7612U:
   `kmod-mt76x2u`; Atheros AR9271: `kmod-ath9k-htc`.
3. Network → Wireless: включите `radio2` (диапазон донгла: 2,4 ГГц у
   донгла 2,4 ГГц; двухдиапазонный работает в одном диапазоне за раз —
   выберите там 5 ГГц), задайте SSID и пароль. Встроенные `radio0` /
   `radio1` можно выключить, в эфир они всё равно не выходят.

Проверено с Ralink RT5370 (148f:5370) на Cudy WR3000P с OpenWrt 25.12.5:
точка доступа на канале 6, WPA2, в мосту br-lan; на Linux и на
Windows 11 с UsbDk.

## Структура репозитория

```
emulator.sh                 запуск под Linux
presets/                  пресеты плат (*.ini)
build.sh                  сборка QEMU (Linux)         → README.build.linux.ru.md
build-windows.sh          пакет для Windows (кросс)   → README.build.windows.ru.md
qemu-patches/             патчи поверх QEMU v10.1.0
tools/                    prepare-nand.sh, mknand.py, host-bridge.sh,
                          gen_dramc_table.py (статусы DRAMC по умолчанию)
tools/uboot-rutc50/       U-Boot пресета Teltonika RUTC50 (исходники,
                          build.sh, собранный FIP)
windows/                  лаунчер, редактор пресетов, терминал (C#),
                          README.txt для пакета
languages/                тексты лаунчера (en.ini, ru.ini)
tests/                    quick.py (проверки по консоли), powercut.py
factory/                  дампы Factory (EEPROM Wi-Fi) и bdinfo (MAC)
wr3000u/, tr3000/, wr3000/ дампы стоковых BL2/FIP (не в git)
m3000/                    свои сборки OpenWrt (не в git)
```

## Заметки для разработчиков

- Исходники, по которым моделировалось железо: дерево Linux из OpenWrt
  (vanilla + патчи OpenWrt), U-Boot, TF-A от mtk-openwrt, mt76, код DRAMC
  MediaTek из coreboot (MT8192/MT8195, то же поколение DRAMC).
- `-d unimp` логирует каждое обращение к регистрам без явной модели —
  самый быстрый способ понять, что опрашивает новая прошивка.
- [`tests/quick.py`](tests/quick.py) запускает QEMU с консолью на сокете и
  ждёт строки в консоли с жёсткими лимитами времени, например
  `tests/quick.py -P cudy-tr3000-v1 --qemu="-netdev user,id=wan" 'Starting kernel@60' 'eth0: Link is Up@120'`.
  [`tests/powercut.py`](tests/powercut.py) выключает/сбрасывает QEMU в
  случайные моменты и проверяет, что BL2 по-прежнему грузит FIP.
- Windows-сборка собирается clang (нативный TLS): эмулируемый TLS в MinGW GCC
  замедлял выполнение гостя примерно на 30 %; кроме того, она оптимизирована
  по профилю (PGO, 5–17 % на настоящей Windows), а лаунчер выводит QEMU из
  режима эффективности Windows 11. Linux-пакеты релизов тоже оптимизированы
  по профилю (GCC, `PGO_NAND=nand-wr3000p ./build.sh`, ~8–10 % на работе
  гостя с частыми системными вызовами). По профилю (`perf`) основное время уходит
  на поиск блоков трансляции в TCG, модели устройств почти не видны; -O3/LTO
  ничего не дали. QEMU уже выполняет каждое из двух ядер гостя в своём
  потоке хоста (MTTCG); один поток гостя на несколько ядер не разложить.
- OpenWrt помечает overlay «готовым» только в конце загрузки; перезагрузка
  раньше заставляет fstools его стереть (как и на железе).

## Ограничения

- Радио Wi-Fi «молчит» (клиентов нет, сканирование пустое); командный
  интерфейс MCU отвечает обобщённо. Offload WED не эмулируется.
- Прерывания PHY не генерируются (изменения линка после загрузки видят
  только драйверы, работающие опросом).
- Нет packet engine EIP-97; PCIe только у MT7987 (карта MT7992); PWM/I2C —
  заглушки.
- Скорость: близка к настоящему роутеру. Ядро WR3000P поднимает линк WAN
  примерно через 18 с после старта на настоящем роутере (MT7981B, 1,3 ГГц),
  примерно через 24 с в эмуляторе на i7-12700H (Linux в виртуальной
  машине) и примерно так же быстро, как на настоящем роутере, на быстром ПК
  с Windows (3.6, с PGO и PAN; TCG).

## Релизы

CI (`.github/workflows/build.yml`) на каждый push собирает пакеты для
Linux и Windows и каждым загружает по одному пресету на каждую конфигурацию
железа, на Linux и на настоящей Windows: WR3000P (DDR4, RTL8221B на GMAC1,
коммутатор), WR3000H (BL2 для DDR3), WR3000S (WAN на порту коммутатора),
TR3000 (без коммутатора, один LAN на встроенном PHY), Netis NX31 (разметка
флеша без bdinfo) и платы на MT7986: Redmi AX6000 (DDR4, WAN на порту
коммутатора), Netcore N60 (DDR3, RTL8221B), N60 Pro (GPY211 на GMAC1 и на
порту 5 коммутатора), GL.iNet GL-MT6000 (eMMC), Huasifei WH3000 Pro eMMC
(разметка eMMC производителя), Bananapi BPi-R4 Lite на
MT7987 (NAND на SPI2, FIP в UBI, внутренний 2.5G PHY), Teltonika RUTC50
(загрузка с SPI-NOR + SPI-NAND, свой U-Boot) и OpenWrt One (загрузка с
SPI-NAND рядом с SPI-NOR, EN8811H). Папки флеша собираются только для этих тестов и в пакеты
не попадают. Чтобы выпустить релиз, измените [`VERSION`](VERSION) и отправьте
в `main`: если тега `v<VERSION>` ещё нет, CI соберёт с PGO, проверит,
создаст тег и релиз на GitHub с `...-linux-x86_64.tar.gz`,
`...-linux-aarch64.tar.gz` и `...-win64.zip`
(без папок флеша). Локально `build-windows.sh` дополнительно собирает
`...-dumps.zip` — только собранные папки флеша (`Router-Emulator/nand-*`,
`nor-*`, `emmc-*`), чтобы распаковать поверх пакета.

## Лицензия

GPL-2.0-or-later, как у QEMU (эмулятор — это набор патчей QEMU и
инструменты вокруг него): см. [LICENSE](LICENSE). Образов прошивок в
репозитории нет; у OpenWrt и прошивок производителей свои лицензии.
