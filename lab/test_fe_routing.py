"""Compile and exercise the exact production helper carried by patch 0059."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FERoutingTests(unittest.TestCase):
    def test_keenetic_mainline_and_new_sdk_irq_layouts(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('C compiler required for production IRQ helper')
        patch = Path(__file__).parent.parent / 'qemu-patches/0059-hw-arm-filogic-preserve-Keenetic-4.9-FE-receive-IRQ.patch'
        section = patch.read_text().split('+++ b/hw/arm/filogic/mtk_eth_irq.h\n', 1)[1]
        header = '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'mtk_eth_irq.h').write_text(header + '\n')
            (root / 'test.c').write_text('''#include "mtk_eth_irq.h"
#include <assert.h>
int main(void) {
    assert(!mtk_eth_sdk_irq_routing(0));
    assert(!mtk_eth_sdk_irq_routing(0x21021000));
    assert(!mtk_eth_sdk_irq_routing(0x21021030));
    assert(mtk_eth_sdk_irq_routing(0x210ffff2));
    return 0;
}
''')
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(root / 'test.c'), '-o', str(root / 'test')], check=True, capture_output=True)
            subprocess.run([str(root / 'test')], check=True, capture_output=True)
