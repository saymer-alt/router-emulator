# Router Emulator (MediaTek MT7981B / Filogic 820, MT7986 / Filogic 830, MT7987 / Filogic 850, MT7988 / Filogic 860 / 880, MT7621)

> **Note.** This is the preserved upstream documentation of
> [stavultras/router-emulator](https://github.com/stavultras/router-emulator),
> moved here unchanged. The repository landing page of this fork is
> [README.md](README.md) (Keenetic Virtual Lab); the upstream Russian doc is
> [README.ru.md](README.ru.md).

**English** · [Русский](README.ru.md) · Build: [Linux](README.build.linux.md) · [Windows](README.build.windows.md)

> **Board presets are a separate archive since 3.6.** The packages no longer
> contain `presets/`. Every release has `Router-Emulator-<version>-presets.zip`
> next to the packages (on GitHub, Google Drive and Yandex Disk): unpack it
> over the package. When only presets change, a presets-only release
> `X.Y.Z` (e.g. 3.6.1) brings just a new presets archive for the X.Y packages.

**Download:** [Router Emulator on Google Drive](https://drive.google.com/drive/folders/173WnINcit9_lMt86FjfKkGMqnZrCZ-Am)
or [on Yandex Disk](https://yadi.sk/d/Re-w1Nm1DBFhOw): the packages of the
latest release, its presets archive (`Router-Emulator-<version>-presets.zip`)
and ready flash folders, one archive per folder
(`Router-Emulator-<folder>.zip`, e.g. `Router-Emulator-nand-wr3000p.zip`),
for all presets except Keenetic KN-1012 and Routerich AX3000 v1 (they need
dumps of your own router, which are not built). Unpack the presets archive
and the folders of the boards you need over a package.

A QEMU machine, `mt7981-router`, that emulates an MT7981B router board at
the hardware level. The board hardware (Ethernet PHYs/switch, flash, RAM
type and size, USB) is configurable, so one machine covers many devices;
**board presets** (`presets/*.ini`) describe concrete routers. Firmware
runs **unmodified**, through the same boot chain as on a real device:

```
BootROM (emulated) → BL2 (MediaTek preloader, DDR training) → BL31 (TF-A)
→ U-Boot → OpenWrt (kernel + rootfs from UBI on SPI-NAND)
```

Both OpenWrt's own bootloader ("ubootmod" layout) and vendor bootloaders
(BL2/FIP dumped from real devices, with NMBM) boot. Everything the
firmware needed was adapted on the emulator side; no firmware is patched.

MT7986A/B (Filogic 830) boards run on a second machine, `mt7986-router`
(preset key `soc=mt7986`): the same peripherals, four cores, the MT7986
BL2 with its own DRAM calibration and size detection, see
[MT7986](#mt7986-filogic-830); MT7987A/B boards on `mt7987-router`
(`soc=mt7987`, see [MT7987](#mt7987-filogic-850)); MT7988A/D boards
(Filogic 880 / 860) on `mt7988-router` (`soc=mt7988`, see
[MT7988](#mt7988-filogic-880--860)); MT7621A boards (MIPS) on
`mt7621-router` in `qemu-system-mipsel` (`soc=mt7621`, see
[MT7621](#mt7621-mips)).

## Quick start

Linux: [README.build.linux.md](README.build.linux.md) (or the
`Router-Emulator-<version>-linux-x86_64.tar.gz` of a release: QEMU
with its libraries, no build needed; unpack the release's
`Router-Emulator-<version>-presets.zip` over it), then

```bash
tools/prepare-nand.sh cudy_wr3000p-v1 25.12.5   # official images -> nand-wr3000p/
./emulator.sh -P list                             # board presets
./emulator.sh -P cudy-wr3000p-v1                  # router console in this terminal
```

Windows: download the release zip (or build it, see
[README.build.windows.md](README.build.windows.md)) and the release's
`Router-Emulator-<version>-presets.zip`, unpack both into the same place, run
`emulator.exe`. The release contains no NAND folders: create them with
`tools/prepare-nand.sh` (Linux or WSL) and copy them next to `emulator.exe`,
or use dumps of a real router.

64-bit Arm Linux (Raspberry Pi 4/5, Arm servers, TV boxes with CoreELEC /
LibreELEC): `Router-Emulator-<version>-linux-aarch64.tar.gz`. It brings its
own C library and a static `bash`, so it needs only an aarch64 kernel: it
also runs under a 32-bit userland (`uname -m` says `aarch64` or `armv8l`;
a 32-bit ARMv7 kernel cannot run it: QEMU 10 emulates 64-bit guests on
64-bit hosts only). Build the flash folders on a PC and copy them over, then
`./bash emulator.sh -P ... -w user -l user` where `bash` is busybox. An
Amlogic S905 box (4x Cortex-A53, 1 GB) boots the WR3000S to LuCI in about
8 minutes.

## Board hardware (machine options)

`-M mt7981-router,nand-dir=DIR,<options>` (or `mt7986-router`) and
`-m <RAM size>` (128M, 256M, 512M, 1G or 2G; 128 MB is untested: there is
no BL2 for it to try, reports welcome):

| Option | Values | Meaning |
|---|---|---|
| `gmac0` | `mt7531` · `an8855` · `mt7988-switch` · `rtl8221b` · `yt8821` · `gpy211` · `en8811h` · `none` | what GMAC0 (mac@0, SGMII0) is wired to; `an8855` = Airoha AN8855 switch at MDIO 1 (CPU port 5, e.g. Routerich BE7200); `mt7988-switch` = the MT7988's built-in 4-port switch; `en8811h` = Airoha EN8811H 2.5G PHY (OpenWrt One) |
| `ports` | e.g. `wan:lan1:lan2:lan3:lan4` | netdev ids of MT7531 ports 0..4, and port 5 with `port5=` (`-` = unused) |
| `port5`, `port5-phy-addr`, `port5-reset-gpio` | `rtl8221b` · `yt8821` · `gpy211` · `none`; MDIO address (5); GPIO | a 2.5G PHY on MT7531 port 5 (SGMII), e.g. Netcore N60 Pro; its netdev is the 6th entry of `ports` |
| `gmac0-port`, `gmac1-port` | netdev id | port of a PHY attached directly to a GMAC |
| `gmac1` | `rtl8221b` · `yt8821` · `gpy211` · `en8811h` · `gphy` · `i2p5ge` · `mt7531-p5` · `none` | GMAC1 (mac@1); `gphy` = MT7981 built-in 1G PHY; `i2p5ge` = MT7987 / MT7988 internal 2.5G PHY; `gpy211` = MaxLinear GPY211C; `mt7531-p5` = SGMII link to MT7531 port 5 (MediaTek reference design, e.g. SNR-CPE-AX2: its vendor firmware takes WAN through it; needs `gmac0=mt7531` and no `port5` PHY) |
| `gmac0-reset-gpio`, `gmac1-reset-gpio` | GPIO, `-1` (default) | hardware reset line of a 2.5G PHY; not wired by default (a missing line is harmless, a wrong one could hold the PHY in reset) |
| `gmac0-phy-addr`, `gmac1-phy-addr` | MDIO address | address of that PHY (default 1 on gmac0, 6 on gmac1) |
| `flash` | `nand` · `nor` · `emmc` | boot flash: SPI-NAND on SPI0 (default), SPI-NOR on SPI2 (MT7986: SPI0) or eMMC (see below) |
| `nor-nand` | `on` | a SPI-NOR **and** a SPI-NAND (Teltonika RUTC50, OpenWrt One): the flash folder holds the NOR partition files, its subfolder `nand` the NAND ones; `flash=nor` / `flash=nand` picks the boot flash (see [SPI-NOR + SPI-NAND](#spi-nor--spi-nand-teltonika-rutc50-openwrt-one)) |
| `nand` | `128` · `256` · `512` | W25N01GV / W25N02KV / W25N04KV SPI-NAND |
| `nand-id` | 2 to 8 bytes in hex | the bytes the SPI-NAND answers to READ ID, repeating after that (default the Winbond ID, e.g. `efaa21`); `c8017f7f7f` = ESMT F50L1G41LB, the chip of e.g. the Cudy WR3000P / WR3000E. The chip still works as the emulated one; vendor firmware may check the ID (MT7621: the parallel NAND's READ ID, see below) |
| `nand-spi` | `0` (default) · `1` · `2` | SPI controller of the SPI-NAND (BPI-R4 Lite: 2) |
| `switch-irq-gpio` | GPIO | EINT of the MT7531 interrupt (default 38 / 66 / 41 for MT7981 / MT7986 / MT7987) |
| `flash=emmc`, `emmc-boot` | MB (4) | eMMC on MSDC0 instead: one image (the first `*.img` of the flash folder, or `-drive if=sd`) = boot0 + boot1 (`emmc-boot` MB each) + user area, size a power of 2 |
| `nor`, `nor-id` | MB (16), JEDEC ID (`ef4018`) | SPI-NOR size and ID, e.g. `204018` = XMC XM25QH128C/D, `c84018` = GD25Q128; `nor=32,nor-id=ef4019` = Winbond W25Q256JV, `nor=64,nor-id=ef4020` = Winbond W25Q512JV (over 16 MB: 4-byte addressing; e.g. a WR3000 with a bigger chip). MT7621: 16, 32 or 64 MB, and without `nor-id` the Winbond chip of that size |
| `nor-uid` | 16 hex digits | MT7621 only: SPI-NOR unique ID (command 4b, default none); stock Cudy firmware of NOR boards (M1300 v2) checks chip ID + unique ID (`/proc/sys/dev/flash_uuid`, e.g. `2040182040-` + 16 digits) against bdinfo, without a match no admin password can be set or checked: set `nor-id` to the chip's ID and `nor-uid=` the 16 digits after the `-` |
| `ddr` | `ddr3` · `ddr4` | soldered DRAM type: a BL2 built for the other type stops the machine, as DRAM init fails on a real board |
| `usb-port` | `none` · `2` · `3` | USB connector (USB 3.0: devices attach at SuperSpeed) |
| `usb-host` | `VID:PID[;VID:PID…]` | pass USB devices of this PC through to the router (USB 2.0 root ports 2, 3, …, also on boards without a connector), e.g. a Wi-Fi dongle, an LTE modem, a USB Ethernet adapter; see "USB Wi-Fi dongle" |
| `pcie-wifi` | `none` · `mt7992` | MT7987 / MT7988: card in the PCIe slot. `mt7992` = MediaTek MT7992 Wi-Fi 7 (2.4 + 5 GHz); OpenWrt drives it with `kmod-mt7996e` and the firmware of `kmod-mt7992-firmware`, which official MT7987 images do not include |
| `reset-gpio`, `wps-gpio` | GPIO | buttons (QOM `/machine/pinctrl` `reset-button`, `wps-button`) |
| `reset-active-high`, `wps-active-high` | `on` | button reads 1 when pressed (default: active low) |
| `reset-hold` | ms | power on with reset held (U-Boot TFTP recovery) |
| `pan` | `on` (default) · `off` | report PAN (Privileged Access Never) in the CPU ID registers. The A53 / A73 cores have none, so OpenWrt's kernel emulates it by switching TTBR0 on every system call, and under TCG each switch flushes the TLB; with `pan=on` the kernel uses the PAN that QEMU implements anyway, system calls get 2.5–3× faster and booting takes ~40 % less host CPU. `off`: the cores' own ID registers |
| `poweroff` | `stop` · `reboot` | `stop` (default): the emulator ends when Linux powers off ("reboot: Power down" on UART0); `reboot`: like a real board, whose firmware cannot power down |
| `gpio-log` | `on` | print GPIO output changes and the LEDs' states |
| `led1` … `led32` | `NAME: COLOUR SOURCE [+ COLOUR SOURCE…]` | front panel LEDs (see below); `led-state` (QOM `/machine`) returns their states |
| `efuse` | file | load the eFuse contents from a dump of a real board (up to 4 KiB, as read from `/sys/bus/nvmem/devices/nvmem0/nvmem`), so calibration and chip data match that board |
| `efuse-uid` | 32 hex digits | set the per-chip unique block, so several emulated boards are not identical |
| `nand-uid` | 32 hex digits | SPI-NAND unique ID (default: derived from the flash folder name); stock Cudy firmware checks chip ID + unique ID (`/proc/sys/dev/flash_uuid`) against `fuuid` in bdinfo (`C801-` + the unique ID + its complement), without a match login is disabled ("Invalid board info!"): set `nand-id=c8017f7f7f` and `nand-uid=` the 32 hex digits after `C801-` |

Front panel LEDs: `led1=Status: red gpio:11:low + white gpio:10:low` is
one lamp with two colours, each on its own GPIO (`:low` = GPIO_ACTIVE_LOW).
Sources: `gpio:N[:low]` (SoC GPIO), `phy:ADDR:N` (LED pin N of the
Ethernet PHY at MDIO address ADDR, as its LED registers program it:
RTL8221B, GPY211; e.g. the WR3000P WAN lamp in current OpenWrt),
`swphy:PORT:N` (LED pin N of the switch's PHY of port PORT: MT7988
built-in switch, e.g. the BPi-R4 port LEDs), `ws2812:BUS:N` (WS2812B RGB
chain on SPI bus BUS, colour `rgb`: Redmi
AX6000), `hc595:BUS:N` (output N of a 74HC595 shift register chain on SPI
bus BUS, Linux gpio-74x164: the Teltonika RUTC50 mobile, SIM and signal
LEDs) and `pwm:N` (PWM channel, pwm-leds: BPi-R4 Lite, OpenWrt One). QEMU samples them
every 50 ms; `qom-get /machine led-state` gives per LED (`;`) and colour
(`,`) `0`, `1`, `b` (blinking) or `#rrggbb` / `b#rrggbb`. The Windows
launcher shows them as a row of lamps above the console; with `-g`
(`gpio-log=on`) QEMU prints every change ("LED Status: white on").
[`tools/dts-leds.py`](tools/dts-leds.py) fills the `led` keys of the
presets from the OpenWrt device tree (`--write --all`).

Network ports are QEMU netdevs with the ids used above (`wan`, `lan1`, …).
Launcher-only preset keys: `lan-ip` (router LAN address, default
192.168.1.1) and `lan-forwards` (default `8080:80,8443:443,8022:22`, PC
port:router port) for the LAN port "this PC only": a QEMU user-mode network in the
router's /24 with `restrict=on` and these forwards from 127.0.0.1. QEMU's
own DHCP server is off there (`dhcp=off`, needs libslirp ≥ 4.7): OpenWrt's
dnsmasq does not serve DHCP on br-lan while another server answers.

## Board presets

A preset is an INI file: `name`, `description`, `soc` (`mt7981` default,
`mt7986`), `ram` (MB), `nand-dir`,
build-only keys (`openwrt=` OpenWrt profile, `openwrt-local=` own build,
`openwrt-stock=` vendor bootloader dumps) and machine options. Both
launchers use them; the Windows launcher has an editor for them (Ethernet,
switch port labels, RAM type/size, boot flash and a second flash
(SPI-NOR + SPI-NAND, `nor-nand`), USB, LEDs (GPIO, PHY, WS2812B, 74HC595,
PWM), button GPIOs with an
"active high" tick — unticked = active low, the default and what most
routers use; saving from the editor drops `;` comments).

| Preset | Ethernet | RAM | NAND | USB | Bootloader |
|---|---|---|---|---|---|
| Cudy WR3000P v1 | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR4 512 MB | 128 MB (ESMT F50L1G41LB) | 2.0 | OpenWrt (U-Boot layout) |
| Cudy WR3000H v1 | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR3 256 MB | 128 MB | – | OpenWrt (U-Boot layout) |
| Cudy WR3000S v1, WR3000E v1 | 5×1G MT7531 (WAN = port 0) | DDR3 256 MB | 128 MB | – | OpenWrt (U-Boot layout) |
| Cudy WBR3000UAX v1 | 5×1G MT7531 (WAN = port 0) | DDR3 512 MB | 128 MB | 3.0 | OpenWrt (U-Boot layout) |
| Cudy WR3000U v1 | 5×1G MT7531 (WAN = port 0) | DDR3 512 MB | 256 MB | 3.0 | vendor (NMBM) |
| Cudy TR3000 v1 | 2.5G WAN RTL8221B on GMAC0 + 1G LAN built-in PHY | DDR3 512 MB | 128 MB | 3.0 | OpenWrt (U-Boot layout) |
| Cudy TR3000 256MB v1 | same, no switch | DDR3 512 MB | 256 MB | 3.0 | vendor (NMBM) |
| Bazis AX3000WM / WD-R3000N-G2A | 2.5G WAN MaxLinear GPY211C on GMAC0 + 1G LAN built-in PHY, no switch | DDR3 256 MB | 128 MB, no bdinfo | 3.0 | OpenWrt |
| Cudy M3000 v1 / v2 (RTL8221B) | 2.5G WAN RTL8221B + 1G LAN built-in PHY | DDR3 256 MB | 128 MB | – | OpenWrt (own build, U-Boot layout) |
| Cudy M3000 v2 (YT8821) | 2.5G WAN Motorcomm YT8821 + 1G LAN built-in PHY | DDR3 256 MB | 128 MB | – | OpenWrt (own build, U-Boot layout) |
| Netis NX30 V2, NX31 | 1G WAN built-in PHY + 3×1G LAN MT7531 | DDR3 256 MB | 128 MB | – | OpenWrt |
| Netis NX32U | 4×1G MT7531 (WAN = port 0) | DDR3 256 MB | 128 MB | 3.0 | OpenWrt |
| Cudy WR3000 v1 | 4×1G MT7531 (WAN = port 0) | DDR3 256 MB | **SPI-NOR 16 MB** (XM25QH128C) | – | vendor |
| Xiaomi Mi Router AX3000T | 4×1G MT7531 (WAN = port 0) | DDR3 256 MB | 128 MB, Xiaomi layout | – | OpenWrt (U-Boot layout) |
| SNR-CPE-AX2 | 5×1G MT7531 (WAN = port 0); GMAC1 linked to switch port 5 (WAN of the SNR firmware) | DDR3 256 MB | 128 MB, no bdinfo | – | OpenWrt; the SNR firmware boots from a dump of your own router's whole NAND (not provided) |
| Huasifei WH3000 Pro NAND | 2.5G LAN RTL8221B + 1G WAN built-in PHY, no switch | DDR4 1 GB | 256 MB, no bdinfo | 3.0 | vendor (NMBM) |
| Huasifei WH3000R NAND | 1G WAN built-in PHY + 3×1G LAN MT7531 | DDR3 512 MB | 256 MB, no bdinfo | 3.0 | vendor (NMBM) |
| Keenetic KN-1012 | 1G WAN built-in PHY + 4×1G LAN MT7531 (SFP not emulated) | DDR4 512 MB | 256 MB | 3.0 | vendor (NMBM) |
| Xiaomi Redmi AX6000 (MT7986A) | 4×1G MT7531 (WAN = port 4) | DDR4 512 MB | 128 MB | – | OpenWrt (U-Boot layout) |
| Netcore N60 (MT7986A) | 2.5G WAN RTL8221B + 4×1G MT7531 | DDR3 256 MB | 128 MB | – | OpenWrt |
| Netcore N60 Pro (MT7986A) | 2.5G WAN GPY211 + 2.5G LAN GPY211 on switch port 5 + 3×1G MT7531 | DDR4 512 MB | 128 MB | 3.0 | OpenWrt |
| Bananapi BPi-R4 Lite (MT7987A) | 2.5G WAN internal PHY + 4×1G MT7531 (SFP cage not emulated); Wi-Fi 7 MT7992 card on PCIe — for Wi-Fi install `kmod-mt7996e kmod-mt7992-firmware` | DDR4 2 GB | 256 MB on SPI2, FIP in UBI | 3.0 | OpenWrt |
| GL.iNet GL-MT6000 (MT7986A) | 2.5G WAN RTL8221B + 2.5G LAN RTL8221B on switch port 5 + 4×1G MT7531 | DDR4 1 GB | **eMMC** | 3.0 | OpenWrt |
| Huasifei WH3000 Pro eMMC | 2.5G LAN RTL8221B + 1G WAN built-in PHY, no switch | DDR4 1 GB | **eMMC**, vendor layout | 3.0 | OpenWrt (RAX3000M eMMC DDR4 build) |
| Routerich AX3000 | 3×1G LAN + WAN MT7531 (WAN = port 4) | DDR3 256 MB | 128 MB, no bdinfo | 2.0 | OpenWrt (U-Boot layout) |
| Routerich AX3000 v1 | 1G WAN built-in PHY + 3×1G LAN MT7531 | DDR3 512 MB | 128 MB, no bdinfo | 3.0 | vendor (own dumps needed) |
| Routerich BE7200 (MT7987A) | 2.5G WAN internal PHY + 3×1G LAN **Airoha AN8855**; Wi-Fi 7 MT7992 on PCIe | DDR4 1 GB | **512 MB** (W25N04KV) | 3.0 | OpenWrt |
| Asus ZenWiFi BT8 (MT7988D, Filogic 860) | 2.5G WAN internal PHY + 2×1G LAN built-in switch (lan3 on GMAC2 and the MT7996 Wi-Fi not emulated) | DDR4 1 GB | 128 MB, FIP and Factory in UBI | 3.0 | OpenWrt (U-Boot layout) |
| TP-Link TL-7DR7230 v1 (MT7988D, Filogic 860) | 2.5G WAN internal PHY + 4×1G LAN built-in switch | DDR4 512 MB | 128 MB, TP-Link layout | – | OpenWrt **snapshot** (no LuCI) |
| Bananapi BPi-R4 (MT7988A, Filogic 880) | WAN + 3×1G LAN built-in switch (SFP+ cages not emulated) | DDR4 2 GB (real: 4 GB) | **eMMC** (`bpi-r4` layout) | 3.0 | OpenWrt |
| Teltonika RUTC50 | 1G WAN built-in PHY + 4×1G LAN MT7531 (the mobile modem is not emulated); LEDs on a **74HC595** | DDR3 512 MB | **SPI-NOR 16 MB boot + SPI-NAND 512 MB** (rutos-a / rutos-b) | 2.0 | OpenWrt RFB BL2 + own U-Boot ([`tools/uboot-rutc50`](tools/uboot-rutc50)) |
| OpenWrt One | 2.5G WAN **Airoha EN8811H** + 1G LAN built-in PHY | DDR4 1 GB | **SPI-NAND 256 MB boot + SPI-NOR 16 MB** recovery | 2.0 | OpenWrt |
| Zbtlink ZBT-WG3526 (16M) (**MT7621A**, MIPS) | 5×1G MT7530 (WAN = port 4); Wi-Fi MT7603E + MT7612E on PCIe (radio silent) | DDR3 512 MB (448 MB usable) | **SPI-NOR 16 MB** | 3.0 | OpenWrt |
| Xiaomi Mi Router 3G (stock U-Boot) (**MT7621A**, MIPS) | 1G WAN + 2×1G LAN MT7530; Wi-Fi MT7603E + MT7612E on PCIe (radio silent) | DDR3 256 MB | **parallel NAND 128 MB** (Xiaomi layout) | 3.0 | stock Xiaomi U-Boot 1.1.3 + OpenWrt |
| Xiaomi Mi Router 3G (Breed) (**MT7621A**, MIPS) | as above | DDR3 256 MB | **parallel NAND 128 MB** (Xiaomi layout) | 3.0 | Breed 1.2 + OpenWrt |
| Xiaomi Mi Router 4 (stock U-Boot) (**MT7621A**, MIPS) | 1G WAN (MT7530 PHY 4 muxed to GMAC1) + 2×1G LAN MT7530; Wi-Fi MT7603E + MT7612E on PCIe (radio silent) | DDR3 128 MB | **parallel NAND 128 MB** (Xiaomi layout) | – | stock Xiaomi U-Boot 1.1.3 + OpenWrt |
| Cudy WR1300S (**MT7621A**, MIPS) | 1G WAN (MT7530 PHY 0 muxed to GMAC1) + 4×1G LAN MT7530; Wi-Fi MT7603E + MT7663E (MT7613BE) on PCIe (radio silent) | DDR3 128 MB | **SPI-NOR 16 MB** | 3.0 | stock Cudy U-Boot 1.1.3 + OpenWrt 25.12.5 (third-party build, the board is not in OpenWrt yet) |
| Cudy M1300 v2 (**MT7621A**, MIPS) | 1G WAN (MT7530 PHY 4 muxed to GMAC1) + 1G LAN MT7530 port 3; Wi-Fi MT7603E + MT7663E (MT7613BE) on PCIe (radio silent) | DDR3 128 MB | **SPI-NOR 16 MB** | – | stock Cudy U-Boot 1.1.3 + OpenWrt; the stock Cudy firmware (LEDE, kernel 4.4) from a dump of your own router's flash |

Netis boards have no bdinfo partition (FIP at 0x380000, ubi at 0x580000;
MACs in Factory): presets set `openwrt-no-bdinfo=1`. The MT7986 boards use
the same layout. Other layouts are given as `openwrt-parts=label:size,...`
(`tools/prepare-nand.sh --parts`), e.g. the Xiaomi AX3000T: BL2, Nvram,
Bdata, Factory, FIP, crash, crash_log, ubi at 0x600000, KF. Its firmware
calls the LAN ports lan2..lan4; the preset's `ports` names the emulator's
connections, so the launcher's LAN (`lan1`) is switch port 1.

## MT7986 (Filogic 830)

`mt7986-router` is the same machine with the MT7986 differences: four
Cortex-A53 cores, HW_CODE 0x7986, pinctrl at 0x1001f000 (101 GPIOs),
eFuse at 0x11d00000, two SPI controllers, no built-in GbE PHY, the MT7531
interrupt on EINT 66. Its BL2 (MediaTek's prebuilt DRAM library, DDR3 and
DDR4) calibrates against the same DRAM controller model, and detects the
DRAM size with the controller's test agent (writes beyond `-m` wrap
around, as on the real chip). The Wi-Fi driver reads back one MT7976
a-die (2.4 + 5 GHz); OpenWrt has no default EEPROM for the MT7986, so
`tools/prepare-nand.sh --soc mt7986` writes a minimal one into an empty
Factory (with a Factory dump of a real board its calibration is used).

```bash
tools/prepare-nand.sh --soc mt7986 --no-bdinfo netcore_n60-pro 25.12.5 nand-n60-pro
./emulator.sh -P netcore-n60-pro
```

## MT7987 (Filogic 850)

`mt7987-router`: UARTs at 0x11000000, SPI0..2 at 0x11007800 /
0x11008800 / 0x11009800, TOP_MISC at 0x10021000, eFuse at 0x11d30000,
NETSYS v3 frame engine (PDMA at 0x6800), the internal 2.5G PHY
(`gmac1=i2p5ge`, MDIO 15; firmware loading and link), LVTS thermal
sensor, boot strap register (DDR type for "comb" BL2s, boot media for
U-Boot's rootdisk choice). The MT7987 BL2 calibrates DRAM against the
same controller model. Options for its boards: `nand-spi=2` (SPI-NAND on
SPI2), `switch-irq-gpio=41`, `-m 2G`.

Wi-Fi is on PCIe: the machine has the Gen3 controller of port 0
(`mtk_pcie.c`: link, configuration TLPs, INTx, MSI capture; QEMU's
root port is the RC) and, with `pcie-wifi=mt7992`, an MT7992 Wi-Fi 7
card in the slot (`mt7992_wifi.c`, radio silent like the built-in Wi-Fi
of the other SoCs: the firmware is downloaded and answered, both bands
come up, hostapd runs, nothing is on the air). The official image has no
driver for it; install it once (WAN must reach the Internet, the
packages stay in the overlay):

```bash
apk update && apk add kmod-mt7996e kmod-mt7992-firmware
reboot
```

Then enable `radio0` / `radio1` and their networks in LuCI (Wi-Fi is off
by default in OpenWrt).

```bash
tools/prepare-nand.sh --soc mt7987 --flash-mb 256 --ubi-fip bananapi_bpi-r4-lite 25.12.5
./emulator.sh -P bananapi-bpi-r4-lite
```

eMMC boards (GL.iNet GL-MT6000) use `flash=emmc`: the MSDC host model
serves TF-A and U-Boot (PIO) and Linux (descriptor DMA); BL2 boots from
eMMC boot partition 0 ("EMMC_BOOT"), the FIP, kernel and rootfs are GPT
partitions. `tools/prepare-nand.sh --soc mt7986 --emmc glinet_gl-mt6000
25.12.5` builds the image with [`tools/mkemmc.py`](tools/mkemmc.py)
(sparse, 1 GB).

The Huasifei WH3000 Pro eMMC (MT7981B) keeps its vendor GPT layout
(u-boot-env, factory, fip, config, kernel, rootfs; `--emmc-layout
wh3000-pro`), and OpenWrt builds only its firmware (`sysupgrade.bin`, a tar
with the kernel FIT and the root squashfs), not its bootloader. The preset
(`openwrt-uboot=`, `--uboot`) takes the OpenWrt BL2 and FIP of the CMCC
RAX3000M eMMC DDR4 (same SoC, boot medium and DRAM type) and writes a
U-Boot environment that boots the FIT in the `kernel` partition; the
kernel finds `root=PARTLABEL=rootfs` through its own device tree, as with
the vendor bootloader. With dumps of the vendor BL2 (`*boot0*.bin`) and FIP
(`*fip*.bin`), `--stock DIR` keeps the vendor bootloader instead:

```bash
tools/prepare-nand.sh --emmc --emmc-layout wh3000-pro --uboot cmcc_rax3000m-emmc-ddr4 huasifei_wh3000-pro-emmc 25.12.5 emmc-wh3000-pro
./emulator.sh -P huasifei-wh3000-pro-emmc
```

## MT7988 (Filogic 880 / 860)

`mt7988-router`: the MT7988A (Filogic 880, 4 cores) and the MT7988D
(Filogic 860: the same die, the board DT disables cpu@3). Four Cortex-A73
cores (TF-A for the MT7988 carries only Cortex-A73 code; QEMU's
`cortex-a73` is the A72 model with the A73 IDs), MCUCFG at 0x100e0000,
UARTs at 0x11000000, SPI0..2 at 0x11007000 / 8000 / 9000, eFuse at
0x11f50000, NETSYS v3 (PDMA at 0x6800), the internal 2.5G PHY
(`gmac1=i2p5ge`, MDIO 15, MT7988 firmware), the **built-in switch**
(`gmac0=mt7988-switch`: 4 ports, its GbE PHYs with eFuse calibration and
LED registers), xHCI ssusb1 (USB 3.0), MSDC (eMMC), PCIe (`pcie-wifi=`),
two LVTS controllers and the boot strap register U-Boot picks
`/chosen/rootdisk-<media>` by. The official BL2 (also the DDR3/DDR4
"comb" ones) calibrates DRAM against the same controller model.

Not emulated: GMAC2 / the 10G USXGMII ports (SFP+ cages, the BT8's lan3),
the SNFI NAND controller (`snand` BL2s: the BPi-R4 runs from eMMC here),
the RT5190A PMIC (cpufreq stays at the boot clock), MT7996 Wi-Fi cards.

```bash
tools/prepare-nand.sh --soc mt7988 --ubi-fip --ubi-factory 0x200000 asus_zenwifi-bt8 25.12.5
./emulator.sh -P asus-zenwifi-bt8
tools/prepare-nand.sh --soc mt7988 --emmc --emmc-layout bpi-r4 bananapi_bpi-r4 25.12.5
./emulator.sh -P bananapi-bpi-r4
```

The Asus ZenWiFi BT8 keeps its MACs and Wi-Fi EEPROM in a UBI volume
`factory` (`--ubi-factory SIZE`, preset key `openwrt-ubi-factory=`). The
BPi-R4's eMMC image follows OpenWrt's `emmc-gpt.bin` (ubootenv, factory,
fip, recovery, production; the sysupgrade FIT goes into `production`).
The TP-Link TL-7DR7230 v1 exists in OpenWrt snapshot only: its preset
has `openwrt-version=snapshot` (snapshot images have no LuCI; SSH works).

## SPI-NOR + SPI-NAND (Teltonika RUTC50, OpenWrt One)

With `nor-nand=on` a board has both flashes: the SPI-NOR on SPI2 and the
SPI-NAND on SPI0 (`nand-spi`). The flash folder holds the NOR partition
files and its subfolder `nand` the NAND ones (same `*mtdN*` naming, each
flash on its own); `flash=nor` or `flash=nand` says which one the BootROM
boots from — the boot switch of the OpenWrt One.

```
nor-rutc50/mt7981.mtd0.BL2.bin … mt7981.mtd8.recovery.bin    SPI-NOR 16 MB
nor-rutc50/nand/mt7981.mtd0.rutos-a.bin, mt7981.mtd1.rutos-b.bin   SPI-NAND 512 MB
```

- **Teltonika RUTC50**: boots from the NOR (BL2, u-boot-env, factory,
  config with the MACs, FIP, bootconfig-a/b, event-log, recovery); the
  system is UBI (kernel, rootfs, rootfs_data) in the NAND partition
  `rutos-a`. OpenWrt has no bootloader for it and OpenWrt's MT7981 SPI-NOR
  U-Boot builds cannot read UBI (RFB) or boot only the NOR (OpenWrt One),
  so the preset uses the OpenWrt MT7981 RFB SPI-NOR BL2 and a U-Boot built
  by [`tools/uboot-rutc50/build.sh`](tools/uboot-rutc50/build.sh)
  (OpenWrt's U-Boot sources and patches, the RFB SPI-NOR config with UBI,
  the RUTC50 flash layout; the FIP in that folder is the result). Its
  environment boots `rutos-a` with `ubi.mtd=rutos-a`, as the Teltonika
  bootloader does, so OpenWrt's sysupgrade and bootcount scripts work. The
  mobile, SIM and signal LEDs are on a 74HC595 on SPI1 (`hc595:1:N`).
- **OpenWrt One**: the official `snand-factory.bin` (BL2 + UBI with FIP,
  ubootenv, recovery, production) and `nor-factory.bin` (recovery BL2,
  Factory with the Wi-Fi EEPROM, FIP and the recovery system). The preset
  boots the NAND; `-o flash=nor` is the boot switch at NOR (recovery
  U-Boot and system). The 2.5G WAN PHY is an Airoha EN8811H
  (`gmac0=en8811h`): Linux loads its MCU firmware over MDIO, then the link
  comes up at 2.5 Gbit/s.

```bash
tools/prepare-nand.sh --nor-nand rutc50 teltonika_rutc50 25.12.5
./emulator.sh -P teltonika-rutc50
tools/prepare-nand.sh --nor-nand openwrt-one openwrt_one 25.12.5
./emulator.sh -P openwrt-one            # -o flash=nor: recovery from the NOR
```

## MT7621 (MIPS)

MediaTek MT7621A boards run on a second QEMU binary,
`qemu-system-mipsel`, machine `mt7621-router` (preset key `soc=mt7621`;
`emulator.sh`, the launcher and `tests/quick.py` pick the binary). The
boot chain is the real one: the CPU starts in the SPI-NOR mapped at
`0x1fc00000`; U-Boot's TPL locks the L2 cache and loads the SPL into it,
the SPL runs MediaTek's DDR init blob in the Frame Engine SRAM, then U-Boot
and OpenWrt; NAND boards boot through the NAND BootROM (stage1 with the DDR
init into the locked L2, then the bootloader). Emulated: MIPS 1004Kc, 2 cores × 2 VPEs (MT ASE: 4 CPUs in Linux, chip ID
MT7621A; `-smp 2`: one core, MT7621S; `-smp 1`), CM/GIC/CPC (cores
powered up by the CPC, VPEs by MT ASE code; the first VPE's CP0 timer on its IP7, the
others' via the GIC), DRAM 128/256/512 MB (above 448 MB at `0x20000000`, as on the
SoC), SPI controller + SPI-NOR, UART, Frame Engine (QDMA/PDMA, NETSYS v1)
with the MT7530 switch (MCM), xHCI (USB 3.0 + 2.0), GPIO with buttons and
LEDs, the PCIe host (three root ports, `pcie0..2=CARD`) with "radio
silent" Wi-Fi cards for the unmodified mt76 drivers: `mt7603e` (MT7603E,
2.4 GHz), `mt7612e` (MT7612E, 5 GHz) and `mt7663e` (MT7663E / MT7613BE,
5 GHz, driver mt7615e: ROM patch and N9 firmware) load their firmware, hostapd
runs, nothing goes on the air; a card without an EEPROM in flash gets a
minimal one from its eFuse with the MAC 80:af:ca:11:22:35 + slot; the
parallel NAND (NFI + ECC, PIO, `flash=nand`, `nand-id=`); the DRAMC's
software test engine, so a vendor stage1 that calibrates the DDR itself
(stock Xiaomi U-Boot) finds its pass windows; `poweroff=stop|halt`
(Linux's `poweroff` / `halt` end the emulator, or it stays halted like the
chip, which cannot switch itself off); the write-back L1 data cache as far
as uncached stores go (`dcache=on`, the default: Breed overruns an
uncached memset into a malloc header and relies on the dirty cached copy;
on from reset until the OS runs user code, so OpenWrt runs at full
speed). Not yet: SD card.

```bash
tools/prepare-nand.sh --soc mt7621 zbtlink_zbt-wg3526-16m 25.12.5   # nor-zbt-wg3526-16m
./emulator.sh -P zbtlink-zbt-wg3526-16m
```

The flash folder holds `mt7621.mtd0.u-boot.bin` (the OpenWrt U-Boot build
`u-boot-mt7621_zbtlink_zbt-wg3526-16m/u-boot-mt7621.bin`), `u-boot-env`,
`factory` (MAC 80:af:ca:11:22:33 at 0xe000: LAN …:33, WAN …:34) and
`firmware` (the sysupgrade.bin). The OpenWrt image has no
`kmod-usb-storage`: for a USB stick install it (`apk add kmod-usb-storage`).

**Xiaomi Mi Router 3G** (two presets, one per bootloader): OpenWrt has no
bootloader of its own for it, so the flash folder is built from a dump of
the router's `Bootloader` partition (stock U-Boot or Breed) and, if there,
its `factory` (the Wi-Fi calibration; the MACs are replaced by
80:af:ca:11:22:33 and up: WAN …:33, LAN …:34, Wi-Fi …:35 / …:36):

```bash
# DIR: *mtd0*.bin or *Bootloader*.bin (+ *factory*.bin)
tools/prepare-nand.sh --soc mt7621 --stock DIR xiaomi_mi-router-3g 25.12.5 nand-mi-router-3g-uboot
./emulator.sh -P xiaomi-mi-router-3g-uboot     # or -breed
```

**Xiaomi Mi Router 4** is built the same way (`xiaomi_mi-router-4`, folder
`nand-mi-router-4`, preset `xiaomi-mi-router-4`); its WAN is the MT7530's
PHY 4 wired through port 5 straight to GMAC1 (PHY muxing), which the
emulator follows from the switch's MTRAP register. Only `Bootloader` and
`factory` are needed: `Config` (the stock U-Boot's environment) and
`Bdata` hold the router's serial number, MAC and passwords, leave them out.

**Cudy WR1300S** (SPI-NOR, stock Cudy U-Boot 1.1.3): not in OpenWrt yet
([PR #22365](https://github.com/openwrt/openwrt/pull/22365)), so the folder
takes an OpenWrt 25.12.5 build for it. The stock U-Boot reads the kernel
through the flash's memory-mapped window at 0x1c000000 and talks to the
SPI controller with automatic chip select, both emulated. Its `bdinfo` holds
the board data (encrypted) and the MAC: it is generated (MAC
80:af:ca:11:22:33), only `u-boot` and `factory` come from the router:

```bash
# DIR: *mtd0*.bin or *u-boot*.bin, *factory*.bin, the *sysupgrade.bin
tools/prepare-nand.sh --soc mt7621 --nor --stock DIR cudy_wr1300s 25.12.5 nor-wr1300s
./emulator.sh -P cudy-wr1300s
```

**Cudy M1300 v2** is in OpenWrt (`cudy_m1300-v2`, folder `nor-m1300-v2`)
and is built the same way, from its `u-boot` and `factory`. Its two ports
are auto-sensing in the stock firmware; OpenWrt makes switch port 3 the LAN
and PHY 4 (through port 5 to GMAC1) the WAN. The stock Cudy firmware (LEDE,
kernel 4.4, MediaTek's vendor Wi-Fi drivers) also runs: split a dump of the
whole 16 MB flash at 0x30000 / 0x40000 / 0x50000 / 0xff0000 into
`mt7621.mtd0.u-boot.bin` ... `mt7621.mtd4.bdinfo.bin` of a folder and pass
it with `-n`. Its LAN address is 192.168.10.1 (`-o lan-ip=192.168.10.1`
with `-l user`), and its console has no shell. The preset has the board's
NOR chip (`nor-id=204018`, XMC XM25QH128D). The stock firmware compares the
chip's unique ID with `bdinfo`: without your router's ID no admin password
can be created or checked ("Invalid password!"). Give it as `nor-uid`, the
16 digits after the `-` of `cat /proc/sys/dev/flash_uuid` on the router:
`-o lan-ip=192.168.10.1,nor-uid=0123456789ABCDEF`.

The folder (`tools/mknand.py mt7621-nand`) holds the stock partitions,
OpenWrt's `kernel1.bin` in `kernel` (0x600000, "System 2") and
`rootfs0.bin` in `ubi`; `kernel_stock` (System 1, the stock firmware's
kernel) stays erased, so the stock U-Boot falls back to System 2 as on a
router with OpenWrt; Breed boots that kernel too. The MediaTek U-Boot
keeps a factory bad block table in the last 4 blocks, outside every
partition (written at the first boot, so partition dumps lack it; without
it the stock U-Boot scans the used flash for bad block marks, takes data
for marks and skips the kernel): when no dump holds those blocks, the
emulator puts an all-good table there.
Any dump `X` may come with an `X.oob` (its spare area, 64 bytes a page).

## What is emulated

All device models live in `hw/arm/filogic/` of the QEMU tree
(patches: [`qemu-patches/`](qemu-patches/), base QEMU v10.1.0).

| Block | Model | Notes |
|---|---|---|
| CPU, GIC | 2× (MT7986, MT7987: 4×) Cortex-A53, MT7988: 4× Cortex-A73 (QEMU's cortex-a73: the A72 model with the A73 IDs and CPUACTLR_EL1) (EL3/EL2), GICv3, arch timer 13 MHz | secondary CPUs started by BL31 through TOPMISC SPMC power-on; PSCI is BL31's |
| BootROM | high-level emulation (`filogic_router.c`) | parses the `SPINAND!` header + GFH `FILE_INFO`, loads BL2 into L2 SRAM, jumps at EL3 |
| DRAM controller | `mtk_sysctl.c` + generated status table | broadcast mode, RTSWCMD/MRW responses, jitter meter, DQS gating lead/lag, RX data eye: MediaTek's binary DRAM calibration finds real windows, BL2 log is clean (DDR3 and DDR4); `DDRCOMMON0` DDR3EN/DDR4EN checked against `ddr=` |
| Clocks, power, misc | `mtk_sysctl.c` | sparse register file + special cases (frequency meter, CPU power-on, TRNG v2, eFuse calibration data, IPPC, thermal sensor ≈45 °C, EIP-97 ID, WED reset bits) |
| APXGPT | `mtk_sysctl.c` | 8 general purpose timers (used by vendor BL2s) |
| TOPRGU | `mtk_toprgu.c` | watchdog with real timeout, SW reset (reboot), reset status kept across reset |
| UART ×3 | QEMU 16550 + MTK extra registers | |
| SPI (IPM) | `mtk_spim.c` | FIFO + DMA, half-duplex spi-mem mode used by TF-A/U-Boot/Linux |
| SPI-NAND | `spinand.c` | W25N01GV (2048+64) / W25N02KV (2048+128) / W25N04KV (512 MB, 2048+128, two planes), on-die ECC, ONFI parameter page, unique ID page; backing store = folder of partition dumps (see below) or one raw image with OOB |
| eMMC | `mtk_msdc.c` + QEMU eMMC | MSDC host: commands, auto CMD23/CMD12, PIO FIFO (TF-A, U-Boot), GPD/BD DMA (Linux); card with boot0/boot1 and user area; BootROM boots `EMMC_BOOT` images from boot0 |
| SPI-NOR | `spinor.c` | 3-byte addressing (up to 16 MB), selectable JEDEC ID, reads 1-1-1/1-1-2/1-1-4/1-2-2/1-4-4, page program, 4K/32K/64K/chip erase, status registers with QE; BootROM boots `SF_BOOT` images from it; same folder backing store; with `nor-nand=on` next to a SPI-NAND |
| Ethernet | `mtk_eth.c` | frame engine: QDMA TX (Linux), PDMA RX, PDMA v2 (U-Boot); TSO + checksum offload; LynxI SGMII PCS ×2; any combination of switch / PHYs on the two GMACs; interrupt routing of mainline Linux and of MediaTek's SDK driver (vendor firmware: `FE_INT_GRP` groups, PDMA RX on the pdma lines, GIC 189..192) |
| Switch | `mtk_eth.c` | MT7531: paged MDIO access, internal PHY indirect access, MTK special tag (DSA), learning FDB, port matrix, VLAN table (MediaTek's SDK switch driver), port 5 to a PHY or to GMAC1, link IRQ → EINT 38. Airoha AN8855: 32-bit register bus over MDIO page 4, internal PHYs at MDIO 1..5, its registers translated into the MT7531 model (CPU port 5). MT7988 built-in switch: the MT7531 model memory mapped at 0x15020000, SoC GbE PHYs (MT7988 IDs, eFuse calibration) |
| PHYs | `mtk_eth.c` | MT7531 GPHY ×5; RTL8221B-VB-CG (C45, temperature sensor); Motorcomm YT8821 (extended registers, UTP/SerDes spaces, 2.5G status); MaxLinear GPY211C (C45, firmware version, mailbox, temperature sensor); Airoha EN8811H (buckpbus window on MDIO page 4, MCU firmware download, 2.5G status, LED registers); MT7981 built-in GbE PHY (calibration handshake); a PHY on MT7531 port 5; optional hardware reset GPIOs |
| GPIO / EINT | `mtk_pinctrl.c` | buttons (reset/WPS, `reset-hold-ms` for timed presses), LED log, pad levels to board devices |
| LEDs | `filogic_leds.c`, `ws2812b.c`, `hc595.c` | front panel LEDs from the preset: GPIO, PHY LED registers (RTL8221B, GPY211, EN8811H, MediaTek GbE / 2.5G PHYs, also of switch ports), WS2812B on SPI, 74HC595 shift registers on SPI (latched on CS), PWM channels; blinking detection; `led-state` |
| USB | QEMU xHCI + MTK IPPC | USB 2.0 / 3.0 connector |
| Wi-Fi | `mtk_wmac.c` | WFDMA rings + emulated WM/WA firmware command interface: firmware loads, both bands come up, hostapd runs, nothing is on the air (scans are empty) |
| PCIe (MT7987, MT7988) | `mtk_pcie.c` | MediaTek Gen3 host: link, CFGNUM configuration TLPs, INTx, MSI capture, with QEMU's PCIe root port |
| Wi-Fi 7 card (MT7987) | `mt7992_wifi.c` | MT7992 for mt7996e: BAR0 remap windows, WFDMA rings, patch/RAM download, UNI command results, TX free events; radio silent |
| Crypto (EIP-97) | ID only | the safexcel driver detects "no packet engine" and disables itself; software crypto is used |
| WED | `mtk_wmac.c`, `mtk_wo.c`, reset bits | for MediaTek's vendor Wi-Fi driver (stock firmware: mt_wifi + mtk_warp): Wi-Fi RX enable and interrupts through WED, and the WO offload CPU as a message stub (CCIF mailbox and rings; every request answered, heartbeat, booted) so the driver does not wait 3 s per message. No offload datapath; OpenWrt's mt7915e uses WED only with `wed_enable=1` (not tested against the WO stub) |

A new `pcap` netdev (`net/pcap.c`, libpcap / Npcap loaded at run time)
attaches a port to a host adapter like a bridged adapter — used by the
Windows launcher.

## Flash (NAND folder)

A NAND folder contains partition dumps without OOB. **Every file whose name
contains `mtdN` becomes partition N**; files are concatenated in order mtd0,
mtd1, … into the full flash, e.g. `mt7981.mtd0.BL2.bin`,
`mt7981.mtd1.u-boot-env.bin`, `mt7981.mtd2.Factory.bin`,
`mt7981.mtd3.bdinfo.bin`, `mt7981.mtd4.FIP.bin`, `mt7981.mtd5.ubi.bin`.

Everything the router writes (settings, sysupgrade, U-Boot env) is written
back into these files. Dumps from a real router (`cat /dev/mtdN >
name.mtdN.label.bin`) can be used directly. With `flash=nor` the folder is
the SPI-NOR contents (e.g. BL2, u-boot-env, Factory, bdinfo, FIP,
firmware); with `nor-nand=on` the NAND files are in its
subfolder `nand`. Files smaller than the flash (a 128 MB dump started with
`nand=256`, say) are completed on the first start: the last file grows
with erased bytes (`0xff`) to the flash size, so what the router writes
there is kept.

- [`tools/prepare-nand.sh`](tools/prepare-nand.sh) `[--stock DIR [--nor] | --local DIR] [--flash-mb N] [--no-bdinfo] PROFILE [VERSION] [OUTDIR]` — builds a NAND folder for an OpenWrt device profile: downloads the official OpenWrt U-Boot images (`PROFILE-ubootmod-*` or `PROFILE-*`, sha256 verified), or uses own builds (`--local`), or keeps a vendor BL2/FIP (`--stock`) with the OpenWrt `sysupgrade.bin` in the vendor layout; `--no-bdinfo` for boards without a bdinfo partition, `--nor` for SPI-NOR boards (vendor BL2/FIP + the OpenWrt `sysupgrade.bin` in the `firmware` partition), `--soc mt7986` for MT7986 boards (partition files `mt7986.mtdN.*`, minimal Wi-Fi EEPROM in an empty Factory: chip ID, MAC, 4 paths, 20 dBm target TX power), `--ubi-fip` for "spim-nand-ubi" BL2s (BL2 at 0, UBI from 0x200000 with the FIP as volume `fip`), `--ubi-factory SIZE` (Factory as UBI volume `factory`, Asus BT8), `--emmc [--emmc-layout L]` (eMMC image, see [MT7988](#mt7988-filogic-880--860)), `--nor-nand rutc50|openwrt-one` (SPI-NOR + SPI-NAND folder, see [above](#spi-nor--spi-nand-teltonika-rutc50-openwrt-one)). Factory/bdinfo come from `factory/`.
- [`tools/mknand.py`](tools/mknand.py) — create / edit images: `create` (BL2, FIP, Factory, bdinfo, UBI from `.itb` or a `sysupgrade.bin`), `write --part fip`, `read`, `split`, `join`, `--flash-mb 256`, `--no-bdinfo`, `nor` (SPI-NOR folder: `--parts` layout, `--raw` whole-chip image, `--mac-part`, `--env` U-Boot environment; with `--nand-parts`, `--nand-file`, `--nand-sysupgrade` also the `nand` subfolder).
- [`tools/kernel-dts.sh`](tools/kernel-dts.sh) — the kernel's MediaTek DTS with the DTS parts of OpenWrt's patches (`work/kdts`), for boards whose DTS is in the kernel tree (BPi-R4); [`tools/dts-leds.py`](tools/dts-leds.py) reads them there.
- [`tools/mkemmc.py`](tools/mkemmc.py) — eMMC image (`flash=emmc`): BL2 in boot0, GPT user area with u-boot-env, factory, fip, kernel, rootfs (GL-MT6000 layout, `--layout wh3000-pro`, or `--layout bpi-r4` with `--fit` / `--recovery` (FIT into production / recovery)), OpenWrt `squashfs-factory.bin` in kernel + rootfs or `--sysupgrade` (sysupgrade tar), `--boot-env` (OpenWrt U-Boot environment that boots the `kernel` partition).

## Running

Linux: [`emulator.sh`](emulator.sh) — `-P PRESET` (`-P list`), `-o OPTS`
(override machine options, `ram=`), `-n NANDDIR`, `-w bridge|user|offline|none`
(`offline` = user-mode WAN with `restrict=on`: DHCP works, nothing leaves the PC),
`-l isolated|nic|user|none` (`user` = this PC only, forwards from the
preset), `-p "1 3"` (LAN ports), `-u DIR` (USB stick from a
folder, FAT16), `-L DIR` (console logs), `-g` (GPIO and LED log), `-R` (power on
with reset held 10 s → TFTP recovery), `-d` (unimplemented register log),
`-S SOCK` (headless: console on a unix socket, for scripts and CI;
`socat -,raw,echo=0,escape=0x1d UNIX-CONNECT:SOCK` to attach). `WAN_EXTRA` /
`LAN_EXTRA` in the environment are appended to the user-mode netdevs, e.g.
`WAN_EXTRA=",guestfwd=tcp:10.0.2.100:80-cmd:nc 127.0.0.1 8000"` gives the
router a local test server even with `-w offline`. Headless example, no
root needed (the router's web UI at http://127.0.0.1:8080/):

```bash
./emulator.sh -P cudy-wr3000p-v1 -w offline -l user -S work/console.sock &
socat -,raw,echo=0,escape=0x1d UNIX-CONNECT:work/console.sock   # console, Ctrl-] detaches
echo quit | socat - UNIX-CONNECT:work/monitor.sock    # stop
```
Host networking: [`tools/host-bridge.sh`](tools/host-bridge.sh) (`br0`
with the NIC for WAN, isolated `br-wrlan` for LAN — LAN on the real
network would expose the router's DHCP/RA there).

Windows: `emulator.exe` — board preset (with editor: New / Edit / Save /
Save as / Delete), NAND folder, WAN/LAN (NAT, "this PC only" port forwards
to LuCI/SSH, or bridge to an adapter via Npcap; when Npcap does not let a
non-administrator open the adapter, it offers to restart as administrator),
USB folder, log folder,
Reset/WPS buttons, "Power + Reset: 10 s" (TFTP recovery), power off on
`poweroff`, built-in terminal (VT100, PuTTY-like 80×24 default with cell
snapping, select = copy, right click = paste, Ctrl+Shift+R fits the router
tty to the window), interface language switched on the fly
([`languages/*.ini`](languages/): English, Русский; add a language by
copying `en.ini`; [`tools/gen-lang-en.py`](tools/gen-lang-en.py)
regenerates `en.ini` from the sources, `--check` lists untranslated keys).

## USB Wi-Fi dongle

The emulated Wi-Fi radios are silent. For real Wi-Fi, pass a USB Wi-Fi
dongle of this PC through to the router (`usb-host=VID:PID`, several
`;` separated; Windows launcher: "USB devices" → Choose…); OpenWrt drives it with its own driver as one
more radio (`radio2`, path `…/usb1/1-2/1-2:1.0`), next to the silent
built-in ones, and runs hostapd on it like on real hardware.

1. Linux: `./emulator.sh … -o usb-host=148f:5370` (QEMU built with
   libusb: `libusb-1.0-0-dev`). QEMU opens `/dev/bus/usb/…`, so the user
   needs write access (emulator.sh prints a udev rule otherwise); the
   host's driver releases the dongle while the router uses it.
   Windows: install [UsbDk](https://github.com/daynix/UsbDk/releases)
   (the dongle stays on its Windows driver and is taken over only while
   the router runs) or bind the WinUSB driver to it with Zadig.
2. In OpenWrt install the dongle's driver once (WAN needs Internet), e.g.
   Ralink RT5370 / RT3070: `apk add kmod-rt2800-usb rt2800-usb-firmware`;
   MediaTek MT7601U: `kmod-mt7601u`; MT7612U: `kmod-mt76x2u`; Atheros
   AR9271: `kmod-ath9k-htc`.
3. Network → Wireless: enable `radio2` (the dongle's band: a 2.4 GHz
   dongle gives 2.4 GHz; a dual-band one works in one band at a time —
   choose 5 GHz there), set SSID and key. The built-in `radio0` /
   `radio1` can be disabled, they are not on the air anyway.

Checked with a Ralink RT5370 (148f:5370) on a Cudy WR3000P with OpenWrt
25.12.5: AP on channel 6, WPA2, bridged to br-lan; on Linux and on
Windows 11 with UsbDk.

## Repository layout

```
emulator.sh                 Linux launcher
presets/                  board presets (*.ini)
build.sh                  QEMU build (Linux)      → README.build.linux.md
build-windows.sh          Windows package (cross) → README.build.windows.md
qemu-patches/             patches on top of QEMU v10.1.0
tools/                    prepare-nand.sh, mknand.py, host-bridge.sh,
                          gen_dramc_table.py (DRAMC status defaults)
tools/uboot-rutc50/       U-Boot of the Teltonika RUTC50 preset (sources,
                          build.sh, the built FIP)
windows/                  launcher, preset editor, terminal (C#),
                          README.txt for the package
languages/                launcher texts (en.ini, ru.ini)
tests/                    quick.py (console-driven checks), powercut.py
factory/                  Factory (Wi-Fi EEPROM) and bdinfo (MAC) dumps
wr3000u/, tr3000/, wr3000/ vendor BL2/FIP dumps (not in git)
m3000/                    own OpenWrt builds (not in git)
```

## Development notes

- Reference sources used to model the hardware: OpenWrt's Linux tree
  (vanilla + OpenWrt patches), U-Boot, mtk-openwrt TF-A, mt76, coreboot's
  MediaTek DRAMC code (MT8192/MT8195, same DRAMC generation).
- `-d unimp` logs every access to registers without explicit modelling —
  the fastest way to find what new firmware polls.
- [`tests/quick.py`](tests/quick.py) starts QEMU with the console on a
  socket and waits for console patterns with hard time limits, e.g.
  `tests/quick.py -P cudy-tr3000-v1 --qemu="-netdev user,id=wan" 'Starting kernel@60' 'eth0: Link is Up@120'`.
  [`tests/powercut.py`](tests/powercut.py) quits/resets QEMU at random
  moments and checks that BL2 still loads FIP.
- The Windows build uses clang (native TLS): MinGW GCC's emulated TLS made
  guest execution ~30 % slower; it is also PGO-optimised (5–17 % on real
  Windows), and the launcher keeps QEMU out of Windows 11 efficiency mode.
  The released Linux packages are PGO-optimised too (GCC,
  `PGO_NAND=nand-wr3000p ./build.sh`, ~8–10 % on system-call heavy guest
  work).
  Profile (`perf`): most time goes to TCG's translation-block lookup, the
  device models are negligible; -O3/LTO gave nothing. QEMU already runs
  each of the two guest CPUs in its own host thread (MTTCG); one guest
  thread cannot be spread over more host cores.
- OpenWrt marks the overlay "ready" only at the end of boot; rebooting
  earlier makes fstools wipe it (same as on hardware).

## Limitations

- Wi-Fi radio is silent (no stations, empty scans); the MCU command
  interface answers generically. WED offload is not emulated.
- PHY interrupts are not generated (link changes after boot are seen only
  by polling drivers).
- No EIP-97 packet engine; PCIe only on MT7987 (the MT7992 card); PWM/I2C
  are stubs.
- Speed: close to the real router. The kernel of a WR3000P brings its WAN
  link up about 18 s after start on the real router (MT7981B, 1.3 GHz),
  about 24 s in the emulator on an i7-12700H (Linux VM) and about as fast
  as on the real router on a fast Windows PC (3.6, with PGO and PAN; TCG).

## Releases

CI (`.github/workflows/build.yml`) builds the Linux and Windows packages on
every push and boots one preset per distinct hardware combination with each
of them, on Linux and on a real Windows runner: WR3000P (DDR4, RTL8221B on
GMAC1, switch), WR3000H (DDR3 preloader), WR3000S (WAN on a switch port),
TR3000 (no switch, one LAN on the built-in PHY), Netis NX31 (flash layout
without bdinfo) and the MT7986 boards Redmi AX6000 (DDR4, WAN on a switch
port), Netcore N60 (DDR3, RTL8221B), N60 Pro (GPY211 on GMAC1 and on
switch port 5), GL.iNet GL-MT6000 (eMMC), Huasifei WH3000 Pro eMMC (vendor
eMMC layout), the MT7987 Bananapi BPi-R4
Lite (NAND on SPI2, FIP in UBI, internal 2.5G PHY), the Teltonika RUTC50
(SPI-NOR boot + SPI-NAND, own U-Boot) and the OpenWrt One (SPI-NAND boot
next to a SPI-NOR, EN8811H). Flash folders are built for these tests only, never
packaged.
To release, change [`VERSION`](VERSION) and push to `main`: when the tag
`v<VERSION>` does not exist yet, CI builds with PGO, tests, creates the tag
and the GitHub Release with `...-linux-x86_64.tar.gz`, `...-linux-aarch64.tar.gz`
and `...-win64.zip`
(no flash folders). Locally `build-windows.sh` also makes `...-dumps.zip`:
only the flash folders it built (`Router-Emulator/nand-*`, `nor-*`,
`emmc-*`), to unpack over a package.

## License

GPL-2.0-or-later, like QEMU (the emulator is a set of QEMU patches plus
tools around it): see [LICENSE](LICENSE). Firmware images are not part of
this repository; OpenWrt and vendor firmware keep their own licenses.
