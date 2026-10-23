// Board presets (presets\*.ini) and the preset editor ("constructor").
//
// A preset file:
//   [preset]
//   name=...            shown in the model list
//   description=...     hardware summary
//   ram=512             MB (-m)
//   nand-dir=nand-xxx   default NAND folder (relative to the program folder)
//   openwrt=...         OpenWrt device profile the package's NAND folder is
//                       built from (build-windows.sh; ignored here)
//   soc=mt7981          SoC: mt7981 (default) or mt7986, the machine is
//                       <soc>-router
//   led1=...            front panel LEDs (see Leds.cs), also machine options
//   key=value           every other key is a machine option
//                       (gmac0, gmac1, ports, nand, ddr, usb-port, ...)

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;

namespace RouterEmulator
{
    class Preset
    {
        public string FilePath;
        // ordered key/value pairs as in the file
        public List<KeyValuePair<string, string>> Values = new List<KeyValuePair<string, string>>();

        public string Get(string k, string def = "")
        {
            foreach (var kv in Values) if (kv.Key == k) return kv.Value;
            return def;
        }

        public void Set(string k, string v)
        {
            for (int i = 0; i < Values.Count; i++) {
                if (Values[i].Key == k) { Values[i] = new KeyValuePair<string, string>(k, v); return; }
            }
            Values.Add(new KeyValuePair<string, string>(k, v));
        }

        public string Name { get { return Get("name", Path.GetFileNameWithoutExtension(FilePath)); } }
        public string Description { get { return Get("description"); } }
        public int RamMB { get { int r; return int.TryParse(Get("ram", "512"), out r) ? r : 512; } }
        public bool HasUsb { get { return Get("usb-port", "2") != "none"; } }
        // QEMU machine: mt7981-router or mt7986-router
        public string Soc
        {
            get {
                string s = Get("soc", "mt7981");
                return s == "mt7986" || s == "mt7987" || s == "mt7988" || s == "mt7621" ? s : "mt7981";
            }
        }
        public string Machine { get { return Soc + "-router"; } }
        // MT7621 (MIPS) boards run on the second QEMU binary
        public string QemuExe { get { return Soc == "mt7621" ? "qemu-system-mipsel.exe" : "qemu-system-aarch64.exe"; } }
        // machine options of the MT7621 machine (the editor writes the
        // Filogic ones, e.g. gmac0/nand, for every preset)
        static readonly string[] Mt7621Keys = { "ports", "ddr", "usb-port", "reset-gpio", "wps-gpio",
            "reset-active-high", "wps-active-high", "pcie0", "pcie1", "pcie2", "flash", "nand-id",
            "nor", "nor-id", "nor-uid", "dcache", "poweroff" };   // + led1..ledN

        // LAN1 "This PC only": router LAN address and port forwards
        // "pcport:routerport,..." from 127.0.0.1 to the router
        public const string DefaultLanIp = "192.168.1.1", DefaultForwards = "8080:80,8443:443,8022:22";
        public string LanIp { get { return Get("lan-ip", DefaultLanIp); } }
        public string LanForwards { get { return Get("lan-forwards", DefaultForwards); } }

        public static bool ValidIp(string s)
        {
            var p = s.Trim().Split('.');
            if (p.Length != 4) return false;
            foreach (var x in p) { int v; if (!int.TryParse(x, out v) || v < 0 || v > 255 || x.Length == 0) return false; }
            int last = int.Parse(p[3]);
            return last > 0 && last < 255;
        }

        // null on a syntax error
        public static List<int[]> ParseForwards(string s)
        {
            var list = new List<int[]>();
            foreach (var item in s.Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries)) {
                var pr = item.Split(':');
                int a, b;
                if (pr.Length != 2 || !int.TryParse(pr[0], out a) || !int.TryParse(pr[1], out b) ||
                    a < 1 || a > 65535 || b < 1 || b > 65535)
                    return null;
                list.Add(new[] { a, b });
            }
            return list;
        }

        // "127.0.0.1:8080/8443/8022 -> 192.168.1.1:80/443/22"
        public string ForwardSummary()
        {
            var pc = new List<string>();
            var rt = new List<string>();
            foreach (var f in ParseForwards(LanForwards) ?? new List<int[]>()) {
                pc.Add(f[0].ToString());
                rt.Add(f[1].ToString());
            }
            if (pc.Count == 0) return "-";
            return "127.0.0.1:" + string.Join("/", pc.ToArray()) + " -> " + LanIp + ":" + string.Join("/", rt.ToArray());
        }

        // QEMU user-mode network on LAN1: a virtual PC in the router's /24
        // (its own addresses chosen not to clash with the router), no
        // outgoing connections, only the forwards from this PC's loopback
        // (dhcp=off: the router is the DHCP server of its LAN)
        public string HostOnlyNetdev(string id)
        {
            string ip = ValidIp(LanIp) ? LanIp.Trim() : DefaultLanIp;
            string net = ip.Substring(0, ip.LastIndexOf('.') + 1);
            int router = int.Parse(ip.Substring(ip.LastIndexOf('.') + 1));
            var free = new List<int>();
            for (int i = 250; i > 0 && free.Count < 3; i--) if (i != router) free.Add(i);
            var sb = new StringBuilder("-netdev user,id=" + id + ",net=" + net + "0/24,host=" + net + free[0]
                + ",dns=" + net + free[1] + ",dhcpstart=" + net + free[2] + ",dhcp=off,restrict=on");
            foreach (var f in ParseForwards(LanForwards) ?? new List<int[]>())
                sb.Append(",hostfwd=tcp:127.0.0.1:" + f[0] + "-" + ip + ":" + f[1]);
            return sb.ToString();
        }

        // -M options: everything but the launcher's own keys
        public string MachineOptions(string baseDir = null)
        {
            var sb = new StringBuilder();
            foreach (var kv in Values) {
                if (kv.Key == "name" || kv.Key == "description" || kv.Key == "ram" || kv.Key == "nand-dir"
                    || kv.Key == "soc"                 // selects the machine
                    || kv.Key.StartsWith("lan-")       // LAN1 "This PC only" network
                    || kv.Key.StartsWith("openwrt"))   // used by the package build only
                    continue;
                if (Soc == "mt7621" && Array.IndexOf(Mt7621Keys, kv.Key) < 0 && !LedDef.IsKey(kv.Key)) continue;
                string v = kv.Value;
                // a relative eFuse dump path is relative to the program folder
                if (kv.Key == "efuse" && baseDir != null && v.Length > 0 && !Path.IsPathRooted(v))
                    v = Path.Combine(baseDir, v);
                sb.Append(',').Append(kv.Key).Append('=').Append(v.Replace(",", ",,"));
            }
            return sb.ToString();
        }

        public override string ToString() { return Name; }

        public static Preset Load(string path)
        {
            var p = new Preset { FilePath = path };
            foreach (var raw in File.ReadAllLines(path)) {
                string line = raw.Trim();
                if (line.Length == 0 || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
                int eq = line.IndexOf('=');
                if (eq > 0) p.Set(line.Substring(0, eq).Trim(), line.Substring(eq + 1).Trim());
            }
            return p;
        }

        public void Save()
        {
            var lines = new List<string> {
                "; Board preset for the Router Emulator.",
                "; Keys other than name/description/ram/nand-dir are -M machine options.",
                "[preset]",
            };
            foreach (var kv in Values) lines.Add(kv.Key + "=" + kv.Value);
            File.WriteAllLines(FilePath, lines.ToArray());
        }

        public static List<Preset> LoadAll(string dir)
        {
            var list = new List<Preset>();
            if (!Directory.Exists(dir)) return list;
            foreach (var f in Directory.GetFiles(dir, "*.ini")) {
                try { list.Add(Load(f)); } catch (Exception) { }
            }
            list.Sort(delegate (Preset a, Preset b) { return string.Compare(a.Name, b.Name, StringComparison.OrdinalIgnoreCase); });
            return list;
        }

        public static string FileNameFor(string name)
        {
            var sb = new StringBuilder();
            foreach (char c in name.ToLowerInvariant())
                sb.Append(char.IsLetterOrDigit(c) && c < 128 ? c : '-');
            string s = sb.ToString().Trim('-');
            while (s.Contains("--")) s = s.Replace("--", "-");
            return (s.Length > 0 ? s : "preset") + ".ini";
        }
    }

    class Choice
    {
        public string Value, Label;
        public Choice(string v, string l) { Value = v; Label = l; }
        public override string ToString() { return Label; }
    }

    // The preset editor
    class PresetForm : Form
    {
        readonly string presetDir, root;
        Preset preset;          // null: new preset
        public Preset Result;   // saved preset (null if deleted / cancelled)
        public bool Deleted;

        TextBox name, desc, nandDir;
        ComboBox soc, gmac0, gmac1, gmac0Port, gmac1Port, port5, port5Port, nandSize, flash2, ddr, ram, usbPort, pcieWifi;
        // MT7621 boards: Wi-Fi cards in the three PCIe slots, data cache model
        ComboBox[] pcieSlot = new ComboBox[3];
        CheckBox dcache;
        // the MT7621 tab for that SoC only (after "Ethernet"); the chip
        // identity one after "Buttons": eFuse and SPI-NAND UID on the
        // Filogic SoCs, the SPI-NOR UID on the MT7621
        TabControl tabs;
        TabPage mipsPage, idPage, ethPage, buttonsPage;
        readonly List<Control> filogicId = new List<Control>(), mt7621Id = new List<Control>();
        // what the board does not have is hidden, with the labels and hints
        // of the control (extras) and the rows below moved up (origTop)
        readonly Dictionary<Control, List<Control>> extras = new Dictionary<Control, List<Control>>();
        readonly HashSet<Control> hidden = new HashSet<Control>();
        readonly Dictionary<Control, int> origTop = new Dictionary<Control, int>();
        ComboBox[] swPort = new ComboBox[5];
        NumericUpDown gmac0Rst, gmac1Rst, port5Rst, port5Addr, resetGpio, wpsGpio;
        TextBox flashId;                // JEDEC ID of the boot flash chip (nand-id / nor-id)
        CheckBox resetHigh, wpsHigh;
        TextBox lanIp, lanFwd, efuseFile, efuseUid, nandUid, norUid;
        ComboBox poweroff;
        CheckBox autoDesc;
        ListBox ledList;

        static readonly string[] PortIds = { "wan", "lan1", "lan2", "lan3", "lan4", "-" };
        // keys written by the editor (dropped when not applicable)
        static readonly List<string> Known = new List<string> {
            "name", "description", "gmac0", "ports", "gmac0-port", "gmac0-reset-gpio", "gmac1",
            "gmac1-port", "gmac1-reset-gpio", "nand", "ddr", "ram", "usb-port", "reset-gpio",
            "wps-gpio", "reset-active-high", "wps-active-high", "lan-ip", "lan-forwards", "nand-dir",
            "flash", "nor", "nor-id", "nor-uid", "poweroff", "efuse", "efuse-uid", "nand-uid", "soc", "port5",
            "port5-phy-addr", "port5-reset-gpio", "pcie-wifi", "nor-nand", "nand-id", "pcie0", "pcie1",
            "pcie2", "dcache" };

        public PresetForm(string presetDir, string root, Preset p)
        {
            this.presetDir = presetDir;
            this.root = root;
            preset = p;
            Text = p == null ? L.T("ed.title_new", "New board preset") : L.F("ed.title", "Board preset: {0}", p.Name);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent;
            ClientSize = new Size(640, 610);
            Font = new Font("Segoe UI", 9f);

            int y = 12;
            name = new TextBox { Left = 150, Top = y, Width = 470 };
            Row(L.T("ed.name", "Name:"), name, ref y);
            desc = new TextBox { Left = 150, Top = y, Width = 470 };
            Row(L.T("ed.description", "Description:"), desc, ref y, 22);
            autoDesc = new CheckBox { Left = 150, Top = y, Width = 470, Text = L.T("ed.auto_desc", "Generate the description from the hardware below") };
            Controls.Add(autoDesc);
            y += 30;

            // hardware on tabs, so the window stays small
            // hardware on tabs, the most used settings on the first one
            tabs = new TabControl { Left = 10, Top = y, Width = 620, Height = 332 };
            Controls.Add(tabs);
            var mem = Page(tabs, L.T("ed.tab_general", "General"));
            var eth = Page(tabs, "Ethernet");
            ethPage = eth;
            // created here, so it is in place before the window has a handle
            // (TabPages.Insert does nothing until then)
            mipsPage = Page(tabs, L.T("ed.tab_mt7621", "PCIe / CPU"));
            int gy = 22;
            gmac0 = Combo(eth, "GMAC0 (mac@0):", ref gy,
                new Choice("mt7531", L.T("ed.mt7531", "MT7531 switch (5 x 1G ports)")),
                new Choice("an8855", L.T("ed.an8855", "Airoha AN8855 switch (5 x 1G ports)")),
                new Choice("mt7988-switch", L.T("ed.mt7988sw", "MT7988 built-in switch (4 x 1G ports)")),
                new Choice("mt7530", L.T("ed.mt7530", "MT7621 built-in MT7530 switch (5 x 1G ports)")),
                new Choice("rtl8221b", L.T("ed.rtl8221b", "RTL8221B 2.5G PHY (Realtek)")),
                new Choice("yt8821", L.T("ed.yt8821", "YT8821 2.5G PHY (Motorcomm)")),
                new Choice("gpy211", L.T("ed.gpy211", "GPY211 2.5G PHY (MaxLinear)")),
                new Choice("en8811h", L.T("ed.en8811h", "EN8811H 2.5G PHY (Airoha)")),
                new Choice("none", L.T("ed.not_connected", "Not connected")));
            var swLabel = new Label { Left = 10, Top = gy + 3, Width = 130, Text = L.T("ed.switch_ports", "Switch ports 0..4:") };
            eth.Controls.Add(swLabel);
            for (int i = 0; i < 5; i++) {
                swPort[i] = new ComboBox { Left = 140 + i * 94, Top = gy, Width = 88, DropDownStyle = ComboBoxStyle.DropDown };
                swPort[i].Items.AddRange(PortIds);
                eth.Controls.Add(swPort[i]);
            }
            Extra(swPort[0], swLabel);
            gy += 30;
            // a 2.5G PHY on switch port 5 (SGMII), e.g. MT7986 boards
            port5 = Combo(eth, L.T("ed.port5", "Switch port 5:"), ref gy,
                new Choice("none", L.T("ed.port5_none", "Not used")),
                new Choice("rtl8221b", L.T("ed.rtl8221b", "RTL8221B 2.5G PHY (Realtek)")),
                new Choice("yt8821", L.T("ed.yt8821", "YT8821 2.5G PHY (Motorcomm)")),
                new Choice("gpy211", L.T("ed.gpy211", "GPY211 2.5G PHY (MaxLinear)")));
            port5.Width = 230;
            var mdio = new Label { Left = 380, Top = gy - 27, Width = 90, Text = L.T("ed.mdio_addr", "MDIO address:") };
            eth.Controls.Add(mdio);
            port5Addr = new NumericUpDown { Left = 470, Top = gy - 30, Width = 60, Minimum = 0, Maximum = 31, Value = 5 };
            eth.Controls.Add(port5Addr);
            Extra(port5Addr, mdio);
            port5Port = PortCombo(eth, L.F("ed.phy_port", "{0} PHY port:", "Port 5"), ref gy, out port5Rst);
            gmac0Port = PortCombo(eth, L.F("ed.phy_port", "{0} PHY port:", "GMAC0"), ref gy, out gmac0Rst);
            gy += 6;
            gmac1 = Combo(eth, "GMAC1 (mac@1):", ref gy,
                new Choice("rtl8221b", L.T("ed.rtl8221b", "RTL8221B 2.5G PHY (Realtek)")),
                new Choice("yt8821", L.T("ed.yt8821", "YT8821 2.5G PHY (Motorcomm)")),
                new Choice("gpy211", L.T("ed.gpy211", "GPY211 2.5G PHY (MaxLinear)")),
                new Choice("en8811h", L.T("ed.en8811h", "EN8811H 2.5G PHY (Airoha)")),
                new Choice("i2p5ge", L.T("ed.i2p5ge", "MT7987 / MT7988 internal 2.5G PHY")),
                new Choice("gphy", L.T("ed.gphy", "MT7981 built-in 1G PHY")),
                new Choice("mt7531-p5", L.T("ed.mt7531_p5", "MT7531 port 5 (SGMII link)")),
                new Choice("none", L.T("ed.not_connected", "Not connected")));
            gmac1Port = PortCombo(eth, L.F("ed.phy_port", "{0} PHY port:", "GMAC1"), ref gy, out gmac1Rst);
            eth.Controls.Add(new Label { Left = 140, Top = gy, Width = 470, Height = 34, ForeColor = Color.DimGray,
                Text = L.T("ed.port_note", "Names of the emulator's connections, not the firmware's labels: the launcher "
                     + "connects its WAN choice to \"wan\" and its LAN choice to \"lan1\".") });

            gy = 22;
            soc = Combo(mem, L.T("ed.soc", "SoC:"), ref gy,
                new Choice("mt7981", "MediaTek MT7981B (Filogic 820)"),
                new Choice("mt7986", "MediaTek MT7986A/B (Filogic 830)"),
                new Choice("mt7987", "MediaTek MT7987A/B (Filogic 850)"),
                new Choice("mt7988", "MediaTek MT7988A/D (Filogic 880/860)"),
                new Choice("mt7621", "MediaTek MT7621A (MIPS)"));
            ddr = Combo(mem, L.T("ed.ram_type", "RAM type:"), ref gy,
                new Choice("ddr4", "DDR4"), new Choice("ddr3", "DDR3"));
            string mb = L.T("ed.mb", "MB"), gb = L.T("ed.gb", "GB");
            ram = Combo(mem, L.T("ed.ram_size", "RAM size:"), ref gy,
                new Choice("128", "128 " + mb), new Choice("256", "256 " + mb), new Choice("512", "512 " + mb),
                new Choice("1024", "1 " + gb),
                new Choice("2048", "2 " + gb));
            // (boot flash list follows)
            // value: "nand:<MB>[:<JEDEC ID>]" or "nor:<MB>:<JEDEC ID>"
            nandSize = Combo(mem, L.T("ed.flash", "Boot flash:"), ref gy,
                new Choice("nand:128", "SPI-NAND 128 " + mb + " (Winbond W25N01GV)"),
                new Choice("nand:128:c8017f7f7f", "SPI-NAND 128 " + mb + " (ESMT F50L1G41LB)"),
                new Choice("nand:256", "SPI-NAND 256 " + mb + " (Winbond W25N02KV)"),
                new Choice("nand:512", "SPI-NAND 512 " + mb + " (Winbond W25N04KV)"),
                new Choice("nor:16:ef4018", "SPI-NOR 16 " + mb + " (Winbond W25Q128JV)"),
                new Choice("nor:16:204018", "SPI-NOR 16 " + mb + " (XMC XM25QH128C/D)"),
                new Choice("nor:16:c84018", "SPI-NOR 16 " + mb + " (GigaDevice GD25Q128)"),
                new Choice("nor:32:ef4019", "SPI-NOR 32 " + mb + " (Winbond W25Q256JV)"),
                new Choice("nor:64:ef4020", "SPI-NOR 64 " + mb + " (Winbond W25Q512JV)"),
                new Choice("emmc:0", L.T("ed.emmc", "eMMC (image *.img in the flash folder)")),
                // value "pnand:<MB>:<READ ID>": MT7621 NFI parallel NAND
                new Choice("pnand:128:c2f18095", L.F("ed.pnand", "Parallel NAND {0} (MT7621, {1})", "128 " + mb, "Macronix MX30LF1G18AC")),
                new Choice("pnand:128:ecf1009540", L.F("ed.pnand", "Parallel NAND {0} (MT7621, {1})", "128 " + mb, "Samsung K9F1G08U0D")));
            // the chip's ID, filled in from the choice and editable (vendor
            // firmware may check it, e.g. Cudy's against bdinfo)
            var idLabel = new Label { Left = 450, Top = gy - 27, Width = 25, Text = "ID:" };
            mem.Controls.Add(idLabel);
            flashId = new TextBox { Left = 475, Top = gy - 30, Width = 90 };
            mem.Controls.Add(flashId);
            Extra(flashId, idLabel);
            // a SPI-NAND next to a SPI-NOR boot flash or the other way round
            // (nor-nand=on, e.g. Teltonika RUTC50, OpenWrt One)
            flash2 = Combo(mem, L.T("ed.flash2", "Second flash:"), ref gy,
                new Choice("none", L.T("ed.none", "None")),
                new Choice("nand:128", "SPI-NAND 128 " + mb + " (Winbond W25N01GV)"),
                new Choice("nand:256", "SPI-NAND 256 " + mb + " (Winbond W25N02KV)"),
                new Choice("nand:512", "SPI-NAND 512 " + mb + " (Winbond W25N04KV)"),
                new Choice("nor:16:ef4018", "SPI-NOR 16 " + mb + " (Winbond W25Q128JV)"),
                new Choice("nor:16:204018", "SPI-NOR 16 " + mb + " (XMC XM25QH128C/D)"),
                new Choice("nor:16:c84018", "SPI-NOR 16 " + mb + " (GigaDevice GD25Q128)"),
                new Choice("nor:32:ef4019", "SPI-NOR 32 " + mb + " (Winbond W25Q256JV)"),
                new Choice("nor:64:ef4020", "SPI-NOR 64 " + mb + " (Winbond W25Q512JV)"));
            var flash2Hint = new Label { Left = 140, Top = gy - 6, Width = 460, Height = 16, ForeColor = Color.DimGray,
                Text = L.T("ed.flash2_hint", "NOR files in the flash folder, NAND files in its subfolder \"nand\"") };
            mem.Controls.Add(flash2Hint);
            Extra(flash2, flash2Hint);
            gy += 14;
            usbPort = Combo(mem, L.T("ed.usb_port", "USB port:"), ref gy,
                new Choice("2", "USB 2.0"), new Choice("3", "USB 3.0"), new Choice("none", L.T("ed.none", "None")));
            // MT7987 boards: a Wi-Fi card in the PCIe slot (the firmware needs its driver packages)
            pcieWifi = Combo(mem, L.T("ed.pcie_wifi", "PCIe Wi-Fi card:"), ref gy,
                new Choice("none", L.T("ed.none", "None")),
                new Choice("mt7992", "MediaTek MT7992 (Wi-Fi 7)"));
            var pcieHint = new Label { Left = 140, Top = gy - 6, Width = 460, Height = 16, ForeColor = Color.DimGray,
                Text = L.T("ed.pcie_wifi_hint", "OpenWrt needs: apk add kmod-mt7996e kmod-mt7992-firmware") };
            mem.Controls.Add(pcieHint);
            Extra(pcieWifi, pcieHint);
            gy += 14;
            mem.Controls.Add(new Label { Left = 10, Top = gy + 3, Width = 130, Text = L.T("ed.nand_dir", "Flash folder:") });
            nandDir = new TextBox { Left = 140, Top = gy, Width = 370 };
            mem.Controls.Add(nandDir);
            var browse = new Button { Left = 516, Top = gy - 1, Width = 84, Height = 25, Text = L.T("main.browse", "Browse...") };
            browse.Click += delegate {
                using (var d = new FolderBrowserDialog { SelectedPath = FullDir(nandDir.Text) }) {
                    if (d.ShowDialog(this) == DialogResult.OK) nandDir.Text = RelDir(d.SelectedPath);
                }
            };
            mem.Controls.Add(browse);
            var adv = Page(tabs, L.T("ed.buttons", "Buttons (GPIO numbers)"));
            buttonsPage = adv;
            adv.Controls.Add(new Label { Left = 10, Top = 25, Width = 60, Text = L.T("ed.reset", "Reset:") });
            resetGpio = new NumericUpDown { Left = 70, Top = 22, Width = 55, Minimum = 0, Maximum = 100 };
            adv.Controls.Add(resetGpio);
            resetHigh = new CheckBox { Left = 135, Top = 23, Width = 160, Text = L.T("ed.active_high", "active high") };
            adv.Controls.Add(resetHigh);
            adv.Controls.Add(new Label { Left = 300, Top = 25, Width = 80, Text = L.T("ed.wps", "WPS / mesh:") });
            wpsGpio = new NumericUpDown { Left = 385, Top = 22, Width = 55, Minimum = 0, Maximum = 100 };
            adv.Controls.Add(wpsGpio);
            wpsHigh = new CheckBox { Left = 450, Top = 23, Width = 160, Text = L.T("ed.active_high", "active high") };
            adv.Controls.Add(wpsHigh);
            var tip = new ToolTip();
            string tipText = L.T("ed.active_high_tip", "Ticked: the GPIO reads 1 while the button is pressed.\n"
                + "Default (unticked): active low, the GPIO reads 0 while pressed.");
            tip.SetToolTip(resetHigh, tipText);
            tip.SetToolTip(wpsHigh, tipText);
            int py = 54;
            poweroff = Combo(adv, L.T("ed.poweroff", "On \"poweroff\":"), ref py,
                new Choice("stop", L.T("ed.poweroff_stop", "Turn the emulator off")),
                new Choice("reboot", L.T("ed.poweroff_reboot", "Reboot (like a real board)")),
                // MT7621: no power-off and no reset on it, the CPU just stops
                new Choice("halt", L.T("ed.poweroff_halt", "Stay halted (like a real MT7621)")));
            poweroff.Left = 140;
            // MT7621 boards: PCIe Wi-Fi cards and the data cache model
            var mips = mipsPage;
            mips.Controls.Add(new Label { Left = 10, Top = 3, Width = 590, Height = 16, ForeColor = Color.DimGray,
                Text = L.T("ed.mt7621_hint", "MT7621 boards: Wi-Fi cards in the three PCIe slots and the CPU data cache model") });
            int my = 22;
            for (int i = 0; i < 3; i++)
                pcieSlot[i] = Combo(mips, L.F("ed.pcie_slot", "PCIe slot {0}:", i), ref my,
                    new Choice("none", L.T("ed.none", "None")),
                    new Choice("mt7603e", "MediaTek MT7603E (2.4 GHz)"),
                    new Choice("mt7612e", "MediaTek MT7612E (5 GHz)"),
                    new Choice("mt7663e", "MediaTek MT7663E / MT7613BE (5 GHz)"));
            mips.Controls.Add(new Label { Left = 140, Top = my - 6, Width = 460, Height = 32, ForeColor = Color.DimGray,
                Text = L.T("ed.pcie_slot_hint", "Radio silent: the driver, firmware and hostapd run, nothing is on the air. "
                     + "The card's EEPROM comes from the factory partition (or its eFuse).") });
            my += 34;
            dcache = new CheckBox { Left = 140, Top = my, Width = 460,
                Text = L.T("ed.dcache", "Model the CPU data cache for uncached stores") };
            mips.Controls.Add(dcache);
            mips.Controls.Add(new Label { Left = 158, Top = my + 24, Width = 440, Height = 48, ForeColor = Color.DimGray,
                Text = L.T("ed.dcache_hint", "Needed by bootloaders that rely on the write-back cache (Breed); "
                     + "only active until the operating system starts, so it costs no speed there.") });
            // chip identity: eFuse dump / per-chip block, SPI-NAND unique ID
            var idg = Page(tabs, L.T("ed.tab_identity", "Chip identity"));
            idPage = idg;
            idg.Controls.Add(new Label { Left = 10, Top = 3, Width = 590, Height = 16, ForeColor = Color.DimGray,
                Text = L.T("ed.identity", "Chip identity (empty = default)") });
            var efLabel = new Label { Left = 10, Top = 25, Width = 130, Text = L.T("ed.efuse", "eFuse dump:") };
            idg.Controls.Add(efLabel);
            efuseFile = new TextBox { Left = 140, Top = 22, Width = 380 };
            idg.Controls.Add(efuseFile);
            var efBrowse = new Button { Left = 526, Top = 21, Width = 84, Height = 25, Text = L.T("main.browse", "Browse...") };
            efBrowse.Click += delegate {
                using (var d = new OpenFileDialog { Title = L.T("ed.efuse", "eFuse dump:") }) {
                    if (d.ShowDialog(this) == DialogResult.OK) efuseFile.Text = RelDir(d.FileName);
                }
            };
            idg.Controls.Add(efBrowse);
            filogicId.AddRange(new Control[] { efLabel, efuseFile, efBrowse });
            efuseUid = UidRow(idg, L.T("ed.efuse_uid", "eFuse UID:"), 55, 16, filogicId);
            nandUid = UidRow(idg, L.T("ed.nand_uid", "NAND UID:"), 85, 16, filogicId);
            var idHint = new Label { Left = 140, Top = 113, Width = 470, Height = 48, ForeColor = Color.DimGray,
                Text = L.T("ed.identity_hint", "eFuse dump of a real board (/sys/bus/nvmem/devices/nvmem0/nvmem); "
                    + "UIDs: 32 hex digits, make several emulated boards different. "
                    + "Vendor firmware may check the NAND UID.") };
            idg.Controls.Add(idHint);
            filogicId.Add(idHint);
            // MT7621: the SPI-NOR's unique ID (command 4Bh), e.g. Cudy's
            // firmware checks it against bdinfo; in the place of the
            // Filogic rows, only one of the two sets is shown
            norUid = UidRow(idg, L.T("ed.nor_uid", "NOR UID:"), 22, 8, mt7621Id);
            var norHint = new Label { Left = 140, Top = 50, Width = 470, Height = 48, ForeColor = Color.DimGray,
                Text = L.T("ed.nor_uid_hint", "MT7621 with a SPI-NOR: 16 hex digits, as the stock firmware's "
                    + "/proc/sys/dev/flash_uuid shows them after the \"-\". Cudy's stock firmware lets you "
                    + "log in only with the UID of your own router.") };
            idg.Controls.Add(norHint);
            mt7621Id.Add(norHint);
            // front panel LEDs (shown above the router console)
            var lp = Page(tabs, L.T("ed.tab_leds", "LEDs"));
            lp.Controls.Add(new Label { Left = 10, Top = 3, Width = 590, Height = 16, ForeColor = Color.DimGray,
                Text = L.T("ed.leds_hint", "Shown above the router console. Presets of OpenWrt boards: "
                    + "tools/dts-leds.py fills them from the device tree.") });
            ledList = new ListBox { Left = 10, Top = 22, Width = 480, Height = 270, IntegralHeight = false,
                HorizontalScrollbar = true };
            lp.Controls.Add(ledList);
            var ledAdd = new Button { Left = 500, Top = 22, Width = 100, Height = 26, Text = L.T("ed.led_add", "Add...") };
            var ledEdit = new Button { Left = 500, Top = 52, Width = 100, Height = 26, Text = L.T("ed.led_edit", "Edit...") };
            var ledDel = new Button { Left = 500, Top = 82, Width = 100, Height = 26, Text = L.T("ed.led_delete", "Delete") };
            var ledUp = new Button { Left = 500, Top = 122, Width = 100, Height = 26, Text = L.T("ed.led_up", "Up") };
            var ledDown = new Button { Left = 500, Top = 152, Width = 100, Height = 26, Text = L.T("ed.led_down", "Down") };
            lp.Controls.AddRange(new Control[] { ledAdd, ledEdit, ledDel, ledUp, ledDown });
            Action edit = () => {
                var cur = ledList.SelectedItem as LedDef;
                if (cur == null) return;
                using (var f = new LedEditForm(cur)) {
                    if (f.ShowDialog(this) == DialogResult.OK) ledList.Items[ledList.SelectedIndex] = f.Result;
                }
            };
            ledAdd.Click += delegate {
                using (var f = new LedEditForm(null)) {
                    if (f.ShowDialog(this) == DialogResult.OK) ledList.SelectedIndex = ledList.Items.Add(f.Result);
                }
            };
            ledEdit.Click += delegate { edit(); };
            ledList.DoubleClick += delegate { edit(); };
            ledDel.Click += delegate {
                int i = ledList.SelectedIndex;
                if (i < 0) return;
                ledList.Items.RemoveAt(i);
                if (ledList.Items.Count > 0) ledList.SelectedIndex = Math.Min(i, ledList.Items.Count - 1);
            };
            EventHandler move = (o, e) => {
                int i = ledList.SelectedIndex, j = i + (o == ledUp ? -1 : 1);
                if (i < 0 || j < 0 || j >= ledList.Items.Count) return;
                var it = ledList.Items[i];
                ledList.Items.RemoveAt(i);
                ledList.Items.Insert(j, it);
                ledList.SelectedIndex = j;
            };
            ledUp.Click += move;
            ledDown.Click += move;
            y += tabs.Height + 8;

            var acc = new GroupBox { Left = 10, Top = y, Width = 620, Height = 90,
                Text = L.T("ed.pc_access", "Access from this PC (LAN port \"This PC only\")") };
            Controls.Add(acc);
            acc.Controls.Add(new Label { Left = 10, Top = 25, Width = 130, Text = L.T("ed.lan_ip", "Router LAN IP:") });
            lanIp = new TextBox { Left = 140, Top = 22, Width = 120 };
            acc.Controls.Add(lanIp);
            acc.Controls.Add(new Label { Left = 270, Top = 25, Width = 125, Text = L.T("ed.forwards", "Port forwards:") });
            lanFwd = new TextBox { Left = 400, Top = 22, Width = 210 };
            acc.Controls.Add(lanFwd);
            acc.Controls.Add(new Label { Left = 140, Top = 50, Width = 470, Height = 34, ForeColor = Color.DimGray,
                Text = L.T("ed.forwards_hint", "PC port:router port, comma separated, e.g. 8080:80,8443:443,8022:22: "
                    + "http://127.0.0.1:8080 opens the router's port 80.") });
            y += acc.Height + 8;

            y += 4;

            var save = new Button { Left = 150, Top = y, Width = 110, Height = 30, Text = L.T("ed.save", "Save") };
            var saveAs = new Button { Left = 266, Top = y, Width = 110, Height = 30, Text = L.T("ed.save_as", "Save as new...") };
            var del = new Button { Left = 382, Top = y, Width = 110, Height = 30, Text = L.T("ed.delete", "Delete"), Enabled = p != null };
            var cancel = new Button { Left = 510, Top = y, Width = 110, Height = 30, Text = L.T("ed.cancel", "Cancel"), DialogResult = DialogResult.Cancel };
            save.Click += delegate { DoSave(preset == null); };
            saveAs.Click += delegate { DoSave(true); };
            del.Click += delegate { DoDelete(); };
            Controls.Add(save); Controls.Add(saveAs); Controls.Add(del); Controls.Add(cancel);
            CancelButton = cancel;
            // taller than the screen: scroll
            int maxH = Screen.PrimaryScreen.WorkingArea.Height - 60;
            AutoScroll = y + 44 > maxH;
            ClientSize = new Size(AutoScroll ? 660 : 640, Math.Min(y + 44, maxH));

            gmac0.SelectedIndexChanged += delegate { UpdateEnabled(); };
            gmac1.SelectedIndexChanged += delegate { UpdateEnabled(); };
            port5.SelectedIndexChanged += delegate { UpdateEnabled(); };
            soc.SelectedIndexChanged += delegate { UpdateEnabled(); };
            nandSize.SelectedIndexChanged += delegate { flashId.Text = ChipId(Val(nandSize)); UpdateEnabled(); };
            flash2.SelectedIndexChanged += delegate { UpdateDesc(); };
            autoDesc.CheckedChanged += delegate { desc.ReadOnly = autoDesc.Checked; UpdateDesc(); };
            foreach (Control c in new Control[] { soc, gmac0, gmac1, gmac0Port, gmac1Port, port5, port5Port, ddr, ram, nandSize, flash2, usbPort, pcieWifi,
                                                   pcieSlot[0], pcieSlot[1], pcieSlot[2] })
                c.TextChanged += delegate { UpdateDesc(); };
            soc.SelectedIndexChanged += delegate { SocChanged(); };
            foreach (var c in swPort) c.TextChanged += delegate { UpdateDesc(); };

            Fill(p ?? Default());
            autoDesc.Checked = p == null;
            UpdateEnabled();
            // the tabs, now that they can be inserted (the handle: to be sure)
            Load += delegate { tabs.Handle.ToString(); UpdateEnabled(); };
        }

        static Preset Default()
        {
            var p = new Preset();
            p.Set("name", "My board");
            p.Set("gmac0", "mt7531");
            p.Set("ports", "lan1:lan2:lan3:lan4:-");
            p.Set("gmac1", "rtl8221b");
            p.Set("gmac1-port", "wan");
            p.Set("nand", "128");
            p.Set("ddr", "ddr4");
            p.Set("ram", "512");
            p.Set("usb-port", "2");
            return p;
        }

        void Row(string label, Control c, ref int y, int step = 30)
        {
            Controls.Add(new Label { Left = 14, Top = y + 3, Width = 135, Text = label });
            Controls.Add(c);
            y += step;
        }

        ComboBox Combo(Control parent, string label, ref int y, params Choice[] items)
        {
            var l = new Label { Left = 10, Top = y + 3, Width = 130, Text = label };
            parent.Controls.Add(l);
            var c = new ComboBox { Left = 140, Top = y, Width = 300, DropDownStyle = ComboBoxStyle.DropDownList };
            c.Items.AddRange(items);
            parent.Controls.Add(c);
            Extra(c, l);
            y += 30;
            return c;
        }

        static TabPage Page(TabControl tabs, string title)
        {
            var tp = new TabPage(title) { UseVisualStyleBackColor = true };
            tabs.TabPages.Add(tp);
            return tp;
        }

        // a UID of "bytes" bytes; its controls also go into "group"
        TextBox UidRow(Control parent, string label, int top, int bytes, List<Control> group)
        {
            var l = new Label { Left = 10, Top = top + 3, Width = 130, Text = label };
            parent.Controls.Add(l);
            var t = new TextBox { Left = 140, Top = top, Width = 280, MaxLength = bytes * 2 };
            parent.Controls.Add(t);
            var r = new Button { Left = 426, Top = top - 1, Width = 100, Height = 25, Text = L.T("ed.random", "Random") };
            group.AddRange(new Control[] { l, t, r });
            r.Click += delegate {
                var b = new byte[bytes];
                new Random().NextBytes(b);
                t.Text = BitConverter.ToString(b).Replace("-", "").ToLowerInvariant();
            };
            parent.Controls.Add(r);
            return t;
        }

        static bool ValidUid(string s, int digits = 32)
        {
            if (s.Length == 0) return true;
            if (s.Length != digits) return false;
            foreach (char c in s) if (Uri.IsHexDigit(c) == false) return false;
            return true;
        }

        ComboBox PortCombo(Control parent, string label, ref int y, out NumericUpDown rst)
        {
            var l = new Label { Left = 10, Top = y + 3, Width = 130, Text = label };
            parent.Controls.Add(l);
            var c = new ComboBox { Left = 140, Top = y, Width = 88, DropDownStyle = ComboBoxStyle.DropDown };
            c.Items.AddRange(PortIds);
            parent.Controls.Add(c);
            Extra(c, l);
            var rl = new Label { Left = 240, Top = y + 3, Width = 230, Text = L.T("ed.phy_reset", "PHY reset GPIO (-1 = not wired):") };
            parent.Controls.Add(rl);
            rst = new NumericUpDown { Left = 470, Top = y, Width = 60, Minimum = -1, Maximum = 100, Value = -1 };
            parent.Controls.Add(rst);
            Extra(rst, rl);
            y += 30;
            return c;
        }

        static void SelectValue(ComboBox c, string v)
        {
            for (int i = 0; i < c.Items.Count; i++)
                if (((Choice)c.Items[i]).Value == v) { c.SelectedIndex = i; return; }
            c.SelectedIndex = 0;
        }

        // (null while Fill() is still selecting the other lists)
        // the JEDEC ID of a boot flash choice ("" for eMMC)
        static string ChipId(string v)
        {
            var f = v.Split(':');
            if (f[0] == "emmc") return "";
            return f.Length > 2 ? f[2] : WinbondNandId(f[1]);
        }

        static string WinbondNandId(string mb)
        {
            return mb == "512" ? "efaa23" : mb == "256" ? "efaa22" : "efaa21";
        }

        static string Val(ComboBox c) { var ch = c.SelectedItem as Choice; return ch != null ? ch.Value : ""; }

        static int Int(string s, int def) { int v; return int.TryParse(s, out v) ? v : def; }

        // QEMU bool option values
        static bool IsOn(string v) { v = v.ToLowerInvariant(); return v == "on" || v == "true" || v == "yes" || v == "1"; }

        static decimal Clamp(NumericUpDown n, int v) { return Math.Max(n.Minimum, Math.Min(n.Maximum, v)); }

        void Fill(Preset p)
        {
            name.Text = p.Name;
            desc.Text = p.Description;
            SelectValue(soc, p.Soc);
            SelectValue(gmac0, p.Soc == "mt7621" ? "mt7530" : p.Get("gmac0", "mt7531"));
            SelectValue(gmac1, p.Get("gmac1", "rtl8221b"));
            string[] ports = p.Get("ports", "lan1:lan2:lan3:lan4").Split(':');
            for (int i = 0; i < 5; i++) swPort[i].Text = i < ports.Length ? ports[i] : "-";
            SelectValue(port5, p.Get("port5", "none"));
            port5Port.Text = ports.Length > 5 ? ports[5] : "lan5";
            port5Addr.Value = Clamp(port5Addr, Int(p.Get("port5-phy-addr"), 5));
            port5Rst.Value = Clamp(port5Rst, Int(p.Get("port5-reset-gpio"), -1));
            gmac0Port.Text = p.Get("gmac0-port", "lan1");
            gmac1Port.Text = p.Get("gmac1-port", "wan");
            gmac0Rst.Value = Clamp(gmac0Rst, Int(p.Get("gmac0-reset-gpio"), -1));
            gmac1Rst.Value = Clamp(gmac1Rst, Int(p.Get("gmac1-reset-gpio"), -1));
            SelectValue(ddr, p.Get("ddr", "ddr4"));
            SelectValue(ram, p.Get("ram", "512"));
            // the boot flash; MT7621 machines default to SPI-NOR
            string bootFlash = p.Get("flash", p.Soc == "mt7621" ? "nor" : "nand");
            string spiNandId = p.Get("nand-id").ToLowerInvariant();
            string spiNand = "nand:" + p.Get("nand", "128");
            if (spiNandId != "" && nandSize.Items.Cast<Choice>().Any(ch => ch.Value == spiNand + ":" + spiNandId))
                spiNand += ":" + spiNandId;
            SelectValue(nandSize, bootFlash == "nor"
                ? "nor:" + p.Get("nor", "16") + ":" + p.Get("nor-id", "ef4018").ToLowerInvariant()
                : bootFlash == "emmc" ? "emmc:0"
                : p.Soc == "mt7621" ? "pnand:128:" + p.Get("nand-id", "ecf1009540").ToLowerInvariant()
                : spiNand);
            // an ID of the preset that is not in the list is kept
            flashId.Text = bootFlash == "nor" ? p.Get("nor-id", "ef4018").ToLowerInvariant()
                : bootFlash == "emmc" ? ""
                : p.Soc == "mt7621" ? p.Get("nand-id", "ecf1009540").ToLowerInvariant()
                : spiNandId != "" ? spiNandId : ChipId(Val(nandSize));
            // the second flash: the type the boot flash is not
            SelectValue(flash2, !IsOn(p.Get("nor-nand")) ? "none"
                : p.Get("flash", "nand") == "nor" ? "nand:" + p.Get("nand", "128")
                : "nor:" + p.Get("nor", "16") + ":" + p.Get("nor-id", "ef4018").ToLowerInvariant());
            SelectValue(usbPort, p.Get("usb-port", "2"));
            SelectValue(pcieWifi, p.Get("pcie-wifi", "none"));
            for (int i = 0; i < 3; i++) SelectValue(pcieSlot[i], p.Get("pcie" + i, "none"));
            dcache.Checked = IsOn(p.Get("dcache", "on"));
            resetGpio.Value = Clamp(resetGpio, Int(p.Get("reset-gpio"), 1));
            wpsGpio.Value = Clamp(wpsGpio, Int(p.Get("wps-gpio"), 0));   // MT7621: 0 = none
            resetHigh.Checked = IsOn(p.Get("reset-active-high"));
            wpsHigh.Checked = IsOn(p.Get("wps-active-high"));
            nandDir.Text = p.Get("nand-dir", "nand");
            lanIp.Text = p.LanIp;
            lanFwd.Text = p.LanForwards;
            SelectValue(poweroff, p.Get("poweroff", "stop"));
            efuseFile.Text = p.Get("efuse");
            efuseUid.Text = p.Get("efuse-uid");
            nandUid.Text = p.Get("nand-uid");
            norUid.Text = p.Get("nor-uid");
            ledList.Items.Clear();
            foreach (var d in LedDef.FromPreset(p)) ledList.Items.Add(d);
        }

        void UpdateEnabled()
        {
            // MT7621: the MT7530 on GMAC0 only (shown, fixed), one boot
            // flash, PCIe slots
            bool m = Val(soc) == "mt7621";
            bool emmc = Val(nandSize).StartsWith("emmc");
            gmac0.Enabled = !m;
            Show(IsSwitch(Val(gmac0)), swPort);
            bool p5on = !m && Val(gmac0) == "mt7531";    // a PHY on port 5: MT7531 only
            bool p5 = p5on && Val(port5) != "none";
            Show(p5on, port5);
            Show(p5, port5Addr, port5Port, port5Rst);
            Show(!m && ExtPhy(Val(gmac0)), gmac0Port, gmac0Rst);
            Show(!m, gmac1);
            Show(!m && Val(gmac1) != "none" && Val(gmac1) != "mt7531-p5", gmac1Port);
            Show(!m && ExtPhy(Val(gmac1)), gmac1Rst);
            Show(HasPcie(Val(soc)), pcieWifi);           // MT7987 / MT7988 machines have the slot
            Show(!m && !emmc, flash2);
            Show(!emmc, flashId);
            ShowTab(mipsPage, ethPage, m);
            // chip identity: what the machine has, the tab only if anything
            bool norId = m && Val(nandSize).StartsWith("nor");
            Show(!m, filogicId.ToArray());
            Show(norId, mt7621Id.ToArray());
            ShowTab(idPage, buttonsPage, !m || norId);
            Relayout(ethPage);
            Relayout(tabs.TabPages[0]);

            UpdateDesc();
        }

        // a tab shown or not; shown: right after the page "after".  Not
        // before the window exists: Windows ignores TabPages.Insert until the
        // TabControl has a handle (Fill() goes through states in between, a
        // page removed there would never come back); Load sets them
        void ShowTab(TabPage page, TabPage after, bool show)
        {
            if (!tabs.IsHandleCreated) return;
            bool placed = tabs.TabPages.IndexOf(page) == tabs.TabPages.IndexOf(after) + 1;
            if (tabs.TabPages.Contains(page) && (!show || !placed))
                tabs.TabPages.Remove(page);
            if (show && !placed)
                tabs.TabPages.Insert(tabs.TabPages.IndexOf(after) + 1, page);
        }

        void Extra(Control c, Control extra)
        {
            if (!extras.ContainsKey(c)) extras[c] = new List<Control>();
            extras[c].Add(extra);
        }

        // shown and usable, or hidden with its labels
        void Show(bool show, params Control[] cs)
        {
            foreach (var c in cs) {
                var all = new List<Control> { c };
                if (extras.ContainsKey(c)) all.AddRange(extras[c]);
                foreach (var x in all) {
                    x.Visible = x.Enabled = show;
                    if (show) hidden.Remove(x); else hidden.Add(x);
                }
            }
        }

        // rows of a tab (from a control at the left edge or a hint of its
        // own down to the next one) without anything shown are left out,
        // the rows below move up
        void Relayout(TabPage page)
        {
            var cs = page.Controls.Cast<Control>().ToList();
            foreach (var c in cs) if (!origTop.ContainsKey(c)) origTop[c] = c.Top;
            var starts = cs.Where(c => c.Left <= 10 || (c is Label && c.ForeColor == Color.DimGray
                                                       && !extras.Values.Any(l => l.Contains(c))))
                           .Select(c => origTop[c] - 5).Distinct().OrderBy(t => t).ToList();
            int y = starts.Count > 0 ? starts[0] : 0;
            for (int i = 0; i < starts.Count; i++) {
                int end = i + 1 < starts.Count ? starts[i + 1] : int.MaxValue;
                var row = cs.Where(c => origTop[c] >= starts[i] && origTop[c] < end).ToList();
                if (row.All(c => hidden.Contains(c))) continue;
                foreach (var c in row) c.Top = y + origTop[c] - starts[i];
                y += i + 1 < starts.Count ? end - starts[i] : 0;
            }
        }

        // another SoC: choices that only exist on one side are replaced
        void SocChanged()
        {
            if (gmac0.SelectedItem == null || nandSize.SelectedItem == null) return;
            bool m = Val(soc) == "mt7621";
            if (m && Val(gmac0) != "mt7530") SelectValue(gmac0, "mt7530");
            if (!m && Val(gmac0) == "mt7530") SelectValue(gmac0, "mt7531");
            string f = Val(nandSize);
            if (m && (f.StartsWith("nand") || f.StartsWith("emmc"))) SelectValue(nandSize, "nor:16:ef4018");
            if (!m && f.StartsWith("pnand")) SelectValue(nandSize, "nand:128");
            if (m && Val(poweroff) == "reboot") SelectValue(poweroff, "halt");
            if (!m && Val(poweroff) == "halt") SelectValue(poweroff, "reboot");
            UpdateEnabled();
        }

        // a switch on GMAC0
        static bool IsSwitch(string v) { return v == "mt7531" || v == "an8855" || v == "mt7988-switch" || v == "mt7530"; }

        static string SwitchName(string v)
        {
            return v == "an8855" ? "AN8855" : v == "mt7988-switch" ? "built-in switch"
                 : v == "mt7530" ? "MT7530" : "MT7531";
        }

        // SoCs with a PCIe slot / the internal 2.5G PHY
        static bool HasPcie(string soc) { return soc == "mt7987" || soc == "mt7988"; }

        // a 2.5G PHY chip with its own reset line
        static bool ExtPhy(string v) { return v == "rtl8221b" || v == "yt8821" || v == "gpy211" || v == "en8811h"; }

        // a 2.5G PHY: external chip or the MT7987 internal one
        static bool Phy25(string v) { return ExtPhy(v) || v == "i2p5ge"; }

        static string PhyName(string v)
        {
            return v == "yt8821" ? "Motorcomm YT8821" : v == "gpy211" ? "MaxLinear GPY211"
                 : v == "en8811h" ? "Airoha EN8811H"
                 : v == "i2p5ge" ? "internal PHY" : "RTL8221B";
        }

        // e.g. "2.5G WAN RTL8221B, 4x1G LAN MT7531, DDR4 512 MB, NAND 128 MB, USB 2.0"
        string Summary()
        {
            var parts = new List<string>();
            bool m = Val(soc) == "mt7621";
            if (Val(soc) != "mt7981") parts.Add(m ? "MT7621A" : Val(soc).ToUpperInvariant());
            int swn = 0; bool swWan = false;
            if (IsSwitch(Val(gmac0))) {
                foreach (var c in swPort) {
                    string t = c.Text.Trim();
                    if (t == "" || t == "-") continue;
                    if (t == "wan") swWan = true; else swn++;
                }
            }
            if (!m && ExtPhy(Val(gmac0))) parts.Add("2.5G " + gmac0Port.Text.Trim().ToUpperInvariant() + " " + PhyName(Val(gmac0)) + " on GMAC0");
            if (!m && Phy25(Val(gmac1))) parts.Add("2.5G " + gmac1Port.Text.Trim().ToUpperInvariant() + " " + PhyName(Val(gmac1)));
            if (Val(gmac0) == "mt7531" && ExtPhy(Val(port5)))
                parts.Add("2.5G " + port5Port.Text.Trim().ToUpperInvariant() + " " + PhyName(Val(port5)) + " on switch port 5");
            if (!m && Val(gmac1) == "gphy") parts.Add("1G " + gmac1Port.Text.Trim().ToUpperInvariant() + " built-in PHY");
            if (IsSwitch(Val(gmac0))) {
                string sn = SwitchName(Val(gmac0));
                if (swWan) parts.Add((swn + 1) + "x1G " + sn + " (WAN = port " + WanPort() + ")");
                else parts.Add(swn + "x1G LAN " + sn);
            }
            // (English: the description is stored in the preset file)
            parts.Add(Val(ddr).ToUpperInvariant() + " " + (Val(ram) == "1024" ? "1 GB" : Val(ram) == "2048" ? "2 GB" : Val(ram) + " MB"));
            var fl = Val(nandSize).Split(':');
            var f2 = Val(flash2).Split(':');
            parts.Add(fl[0] == "emmc" ? "eMMC" : (fl[0] == "nor" ? "SPI-NOR " : f2[0] == "nor" ? "SPI-NAND " : "NAND ")
                + fl[1] + " MB" + (fl[0] != "emmc" && fl[0] != "pnand" && f2[0] != "none" && f2[0] != fl[0]
                ? " + " + (f2[0] == "nor" ? "SPI-NOR " : "SPI-NAND ") + f2[1] + " MB" : ""));
            parts.Add(Val(usbPort) == "none" ? "no USB" : "USB " + Val(usbPort) + ".0");
            if (HasPcie(Val(soc)) && Val(pcieWifi) == "mt7992") parts.Add("Wi-Fi 7 MT7992 on PCIe");
            if (Val(soc) == "mt7621") {
                var cards = new List<string>();
                foreach (var c in pcieSlot) {
                    if (Val(c) == "mt7603e") cards.Add("MT7603E 2.4 GHz");
                    if (Val(c) == "mt7612e") cards.Add("MT7612E 5 GHz");
                    if (Val(c) == "mt7663e") cards.Add("MT7663E 5 GHz");
                }
                if (cards.Count > 0) parts.Add("Wi-Fi " + string.Join(" + ", cards.ToArray()) + " on PCIe (radio silent)");
            }
            return string.Join(", ", parts.ToArray());
        }

        int WanPort()
        {
            for (int i = 0; i < 5; i++) if (swPort[i].Text.Trim() == "wan") return i;
            return -1;
        }

        void UpdateDesc()
        {
            if (autoDesc != null && autoDesc.Checked && soc.SelectedItem != null && port5.SelectedItem != null &&
                gmac0.SelectedItem != null && gmac1.SelectedItem != null &&
                ddr.SelectedItem != null && ram.SelectedItem != null && nandSize.SelectedItem != null && usbPort.SelectedItem != null)
                desc.Text = Summary();
        }

        string FullDir(string d) { return Path.IsPathRooted(d) ? d : Path.Combine(root, d); }

        string RelDir(string d)
        {
            string r = root.TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            return d.StartsWith(r, StringComparison.OrdinalIgnoreCase) ? d.Substring(r.Length) : d;
        }

        Preset Build()
        {
            var p = new Preset();
            p.Set("name", name.Text.Trim());
            p.Set("description", desc.Text.Trim());
            if (Val(soc) != "mt7981") p.Set("soc", Val(soc));
            bool m = Val(soc) == "mt7621";
            if (!m) p.Set("gmac0", Val(gmac0));
            if (IsSwitch(Val(gmac0))) {
                var ports = new List<string>();
                foreach (var c in swPort) ports.Add(c.Text.Trim().Length > 0 ? c.Text.Trim() : "-");
                if (Val(gmac0) == "mt7531" && Val(port5) != "none") {
                    ports.Add(port5Port.Text.Trim().Length > 0 ? port5Port.Text.Trim() : "-");
                    p.Set("ports", string.Join(":", ports.ToArray()));
                    p.Set("port5", Val(port5));
                    p.Set("port5-phy-addr", port5Addr.Value.ToString());
                    if (port5Rst.Value >= 0) p.Set("port5-reset-gpio", port5Rst.Value.ToString());
                } else {
                    p.Set("ports", string.Join(":", ports.ToArray()));
                }
            } else if (ExtPhy(Val(gmac0))) {
                p.Set("gmac0-port", gmac0Port.Text.Trim());
                if (gmac0Rst.Value >= 0) p.Set("gmac0-reset-gpio", gmac0Rst.Value.ToString());
            }
            if (!m) p.Set("gmac1", Val(gmac1));
            if (!m && Val(gmac1) != "none" && Val(gmac1) != "mt7531-p5") {
                p.Set("gmac1-port", gmac1Port.Text.Trim());
                if (ExtPhy(Val(gmac1)) && gmac1Rst.Value >= 0)
                    p.Set("gmac1-reset-gpio", gmac1Rst.Value.ToString());
            }
            var flash = Val(nandSize).Split(':');
            string id = flashId.Text.Trim().ToLowerInvariant();
            if (flash[0] == "nor") {
                p.Set("flash", "nor");
                p.Set("nor", flash[1]);
                p.Set("nor-id", id);
                if (m && norUid.Text.Trim().Length > 0) p.Set("nor-uid", norUid.Text.Trim().ToLowerInvariant());
            } else if (flash[0] == "emmc") {
                p.Set("flash", "emmc");
            } else if (flash[0] == "pnand") {
                p.Set("flash", "nand");
                p.Set("nand-id", id);
            } else {
                p.Set("nand", flash[1]);
                // only another chip than the Winbond one of that size
                if (id != WinbondNandId(flash[1])) p.Set("nand-id", id);
                if (Val(soc) == "mt7621") p.Set("flash", "nand");   // not the default there
            }
            var second = Val(flash2).Split(':');
            if (!m && flash[0] != "emmc" && second[0] != "none" && second[0] != flash[0]) {
                p.Set("nor-nand", "on");
                if (second[0] == "nor") {
                    p.Set("nor", second[1]);
                    p.Set("nor-id", second[2]);
                } else {
                    p.Set("nand", second[1]);
                }
            }
            p.Set("ddr", Val(ddr));
            p.Set("ram", Val(ram));
            p.Set("usb-port", Val(usbPort));
            if (HasPcie(Val(soc)) && Val(pcieWifi) != "none") p.Set("pcie-wifi", Val(pcieWifi));
            p.Set("reset-gpio", resetGpio.Value.ToString());
            if (!m || wpsGpio.Value > 0) p.Set("wps-gpio", wpsGpio.Value.ToString());
            if (m) {
                for (int i = 0; i < 3; i++)
                    if (Val(pcieSlot[i]) != "none") p.Set("pcie" + i, Val(pcieSlot[i]));
                if (!dcache.Checked) p.Set("dcache", "off");
            }
            if (resetHigh.Checked) p.Set("reset-active-high", "on");
            if (wpsHigh.Checked) p.Set("wps-active-high", "on");
            p.Set("lan-ip", lanIp.Text.Trim());
            p.Set("lan-forwards", lanFwd.Text.Trim().Replace(" ", ""));
            p.Set("poweroff", Val(poweroff));
            if (!m) {           // Filogic machines only
                if (efuseFile.Text.Trim().Length > 0) p.Set("efuse", efuseFile.Text.Trim());
                if (efuseUid.Text.Trim().Length > 0) p.Set("efuse-uid", efuseUid.Text.Trim().ToLowerInvariant());
                if (nandUid.Text.Trim().Length > 0) p.Set("nand-uid", nandUid.Text.Trim().ToLowerInvariant());
            }
            p.Set("nand-dir", nandDir.Text.Trim());
            // keep keys this editor does not know (openwrt=..., new options)
            if (preset != null) {
                foreach (var kv in preset.Values)
                    if (p.Get(kv.Key, null) == null && !Known.Contains(kv.Key) && !LedDef.IsKey(kv.Key))
                        p.Set(kv.Key, kv.Value);
            }
            for (int i = 0; i < ledList.Items.Count; i++) p.Set("led" + (i + 1), ledList.Items[i].ToString());
            return p;
        }

        // the same port name on two ports would leave one of them unconnected
        string CheckPorts(Preset p)
        {
            var seen = new List<string>();
            var names = new List<string>();
            if (IsSwitch(p.Get("gmac0"))) names.AddRange(p.Get("ports").Split(':'));
            if (ExtPhy(p.Get("gmac0"))) names.Add(p.Get("gmac0-port"));
            if (p.Get("gmac1") != "none" && p.Get("gmac1") != "mt7531-p5") names.Add(p.Get("gmac1-port"));
            foreach (var n in names) {
                if (n == "-" || n == "") continue;
                if (seen.Contains(n)) return L.F("ed.port_twice", "Port name \"{0}\" is used twice.", n);
                seen.Add(n);
            }
            return null;
        }

        void DoSave(bool asNew)
        {
            var p = Build();
            if (p.Name.Length == 0) { MessageBox.Show(this, L.T("ed.enter_name", "Enter a name."), Text); return; }
            string err = CheckPorts(p);
            if (err == null && p.Get("gmac1") == "mt7531-p5" &&
                (p.Get("gmac0") != "mt7531" || (p.Get("port5", "none") != "none")))
                err = L.T("ed.mt7531_p5_need", "GMAC1 on MT7531 port 5 needs the MT7531 on GMAC0 and no PHY on port 5.");
            string fid = flashId.Text.Trim();
            int fidLen = Val(nandSize).StartsWith("pnand") ? -1 : Val(nandSize).StartsWith("nor") ? 6 : 0;
            if (err == null && flashId.Enabled && (!System.Text.RegularExpressions.Regex.IsMatch(fid, "^[0-9a-fA-F]+$") ||
                (fidLen == 6 && fid.Length != 6) || (fidLen == 0 && (fid.Length < 4 || fid.Length > 16 || fid.Length % 2 != 0)) ||
                (fidLen < 0 && fid.Length != 8 && fid.Length != 10)))
                err = L.T("ed.bad_flash_id", "The flash chip ID must be hex digits: SPI-NAND 4 to 16 (even), SPI-NOR 6, parallel NAND 8 or 10.");
            if (err == null && p.Soc != "mt7981" && p.Get("gmac1") == "gphy")
                err = L.T("ed.no_gphy", "Only the MT7981 has a built-in 1G PHY: choose another GMAC1 PHY.");
            if (err == null && !HasPcie(p.Soc) && (p.Get("gmac0") == "i2p5ge" || p.Get("gmac1") == "i2p5ge"))
                err = L.T("ed.no_i2p5ge", "Only the MT7987 and MT7988 have an internal 2.5G PHY.");
            if (err == null && p.Soc == "mt7621" && (Val(nandSize).StartsWith("nand") || Val(nandSize).StartsWith("emmc")))
                err = L.T("ed.mt7621_flash", "The MT7621 boots from a SPI-NOR or a parallel NAND.");
            if (err == null && (p.Soc == "mt7621") != (Val(poweroff) == "halt") && Val(poweroff) != "stop")
                err = p.Soc == "mt7621"
                    ? L.T("ed.poweroff_mt7621", "The MT7621 cannot reboot itself on poweroff: choose \"Stay halted\".")
                    : L.T("ed.poweroff_filogic", "\"Stay halted\" is the MT7621's: Filogic boards reboot.");
            if (err == null && p.Soc == "mt7621" && Int(Val(ram), 0) > 512)
                err = L.T("ed.mt7621_ram", "The MT7621 has at most 512 MB of RAM.");
            if (err == null && p.Soc != "mt7621" && Val(gmac0) == "mt7530")
                err = L.T("ed.mt7530_only", "The MT7530 switch is the MT7621's: choose another GMAC0 device.");
            if (err == null && p.Soc != "mt7621" && Val(nandSize).StartsWith("pnand"))
                err = L.T("ed.pnand_mt7621", "A parallel NAND is only on the MT7621 (Filogic SoCs use SPI-NAND).");
            if (err == null && p.Soc != "mt7988" && p.Get("gmac0") == "mt7988-switch")
                err = L.T("ed.no_mt7988sw", "Only the MT7988 has the built-in switch.");
            if (err == null && Val(flash2) != "none" && !Val(nandSize).StartsWith("emmc") &&
                Val(flash2).Split(':')[0] == Val(nandSize).Split(':')[0])
                err = L.T("ed.flash2_same", "The second flash must be of the other type (SPI-NOR + SPI-NAND).");
            if (err == null && Val(flash2) != "none" && p.Soc == "mt7986")
                err = L.T("ed.flash2_mt7986", "On the MT7986 the SPI-NOR and the SPI-NAND are both on SPI0.");
            if (err == null && (!ValidUid(efuseUid.Text.Trim()) || !ValidUid(nandUid.Text.Trim())))
                err = L.T("ed.bad_uid", "A UID must be 32 hex digits (or empty).");
            if (err == null && norUid.Enabled && !ValidUid(norUid.Text.Trim(), 16))
                err = L.T("ed.bad_nor_uid", "The NOR UID must be 16 hex digits (or empty).");
            if (err == null && !Preset.ValidIp(p.LanIp))
                err = L.F("ed.bad_ip", "\"{0}\" is not an IPv4 address of a host.", p.LanIp);
            if (err == null && Preset.ParseForwards(p.LanForwards) == null)
                err = L.T("ed.bad_forwards", "Port forwards must look like 8080:80,8443:443,8022:22 (ports 1..65535).");
            if (err != null) { MessageBox.Show(this, err, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning); return; }
            if (asNew) {
                p.FilePath = Path.Combine(presetDir, Preset.FileNameFor(p.Name));
                if (File.Exists(p.FilePath) &&
                    MessageBox.Show(this, L.F("ed.replace", "A preset file {0} already exists. Replace it?", Path.GetFileName(p.FilePath)),
                                    Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                    return;
            } else {
                p.FilePath = preset.FilePath;
            }
            try {
                Directory.CreateDirectory(presetDir);
                p.Save();
            } catch (Exception e) {
                MessageBox.Show(this, L.F("ed.cannot_save", "Cannot save the preset:\n{0}", e.Message), Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            Result = p;
            DialogResult = DialogResult.OK;
        }

        void DoDelete()
        {
            if (MessageBox.Show(this, L.F("ed.ask_delete", "Delete the preset \"{0}\" ({1})? The flash folder is not touched.",
                                preset.Name, Path.GetFileName(preset.FilePath)), Text,
                                MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                return;
            try {
                File.Delete(preset.FilePath);
            } catch (Exception e) {
                MessageBox.Show(this, L.F("ed.cannot_delete", "Cannot delete:\n{0}", e.Message), Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            Deleted = true;
            DialogResult = DialogResult.OK;
        }
    }
}
