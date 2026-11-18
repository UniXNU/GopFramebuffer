#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Run the display's actual console hand-off against the parts of XNU's console it touches."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / 'NativeLegacyDisplay.cpp'
text = source.read_text()
start = '    void handOffConsole() {'
if start not in text:
    raise SystemExit('FAIL: NativeLegacyDisplay.cpp has no handOffConsole method')
at = text.index(start)
method = text[at:text.index('\n    }\n', at)] + '\n    }'
if 'handOffConsole();' not in text[:at] + text[at + len(method):]:
    raise SystemExit('FAIL: nothing calls handOffConsole')

# osfmk/console/video_console.c, as far as the hand-off reaches it. On macOS the kernel starts its progress meter when it takes the
# screen at boot (vc_progress_set(TRUE, ...) from kPEAcquireScreen) and a timer draws the next piece of the bar every 125 ms
# (vc_progressmeter_task), for about 24 minutes. That task looks at vc_progressmeter_enable only: kPEDisableScreen (gc_pause) clears
# vc_progress_enable and leaves both the timer and the meter on. vc_progress_set(FALSE, 0) cancels the timer when it finds
# vc_progress_enable still set, and turns the meter off.
harness = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#define FALSE 0
enum {kPEDisableScreen=3};
struct Console {
    bool progressEnable=true,meterOn=true,timerArmed=true,paused=false;
    unsigned drawn=0,calls=0,stoppedAt=0,pausedAt=0;
    // One period of the meter's timer: what vc_progressmeter_task does when it runs.
    void tick() {if(timerArmed && meterOn)++drawn;}
};
static Console console;
[[maybe_unused]] static void vc_progress_set(int enable,uint32_t) {
    if(enable)return;
    console.stoppedAt=++console.calls;
    if(console.progressEnable){console.progressEnable=false;console.timerArmed=false;}
    console.meterOn=false;
}
static void PE_initialize_console(void *,int op) {
    if(op!=kPEDisableScreen)return;
    console.pausedAt=++console.calls;
    console.paused=true;console.progressEnable=false;
}
struct Display {
    bool consoleReleased=false;
#include "method.inc"
};
int main() {
    // Pausing the console alone leaves the meter drawing: the white mark on a display that sleeps with its backlight on.
    console.tick();assert(console.drawn==1);
    PE_initialize_console(nullptr,kPEDisableScreen);
    console.tick();assert(console.drawn==2 && console.meterOn && console.timerArmed);

    console=Console();
    Display d;
    d.handOffConsole();
    assert(d.consoleReleased && console.paused);
    // The meter goes before the console is paused, while the kernel still cancels its timer.
    assert(console.stoppedAt==1 && console.pausedAt==2);
    assert(!console.meterOn && !console.timerArmed);
    for(unsigned i=0;i<8;++i)console.tick();
    assert(!console.drawn);
    // Later frames leave the console alone.
    d.handOffConsole();d.handOffConsole();
    assert(console.calls==2);
    std::puts("PASS: the hand-off stops the kernel's progress meter, then pauses the console, once");
}
'''

with tempfile.TemporaryDirectory(prefix='gop-console-handoff-') as directory:
    root = Path(directory)
    (root / 'method.inc').write_text(method)
    (root / 'test.cpp').write_text(harness)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                    str(root / 'test.cpp'), '-o', str(root / 'test')], check=True)
    subprocess.run([str(root / 'test')], check=True)
