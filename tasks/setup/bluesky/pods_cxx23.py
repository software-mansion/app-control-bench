#!/usr/bin/env python3
"""Raise ONLY RNReanimated to C++23 in the Pods project.

Xcode 26's libc++ rejects any TU including both <atomic> and <stdatomic.h> below C++23, which
REANodesManager.mm does. Passing CLANG_CXX_LANGUAGE_STANDARD=c++23 to xcodebuild fixes Reanimated
but applies workspace-wide, and React-hermes does not compile under C++23 (unique_ptr<Impl> of an
incomplete type). So scope the bump to the one target that needs it.

CocoaPods writes the standard into the Pods pbxproj build settings, which override the target's
xcconfig - so this has to be edited here. `pod install` regenerates the project and reverts it.
"""
import re
import sys

PATH = "Pods/Pods.xcodeproj/project.pbxproj"
TARGET = "RNReanimated"

s = open(PATH).read()

m = re.search(r"([0-9A-F]{12,32}) /\* " + TARGET + r" \*/ = \{\s*\n\s*isa = PBXNativeTarget[\s\S]*?\n\t\t\};", s)
if not m:
    sys.exit("target %s not found" % TARGET)
cfg_list = re.search(r"buildConfigurationList = ([0-9A-F]{12,32})", m.group(0)).group(1)
cfgs = re.search(cfg_list + r" /\*[^\n]*\*/ = \{[\s\S]*?buildConfigurations = \(([\s\S]*?)\);", s)
ids = re.findall(r"([0-9A-F]{12,32})", cfgs.group(1))
print("  %s build configurations: %s" % (TARGET, ids))

out, changed = s, 0
for cid in ids:
    blk = re.search(cid + r" /\*[^\n]*\*/ = \{[\s\S]*?\n\t\t\};", out)
    body = blk.group(0)
    new = re.sub(r'CLANG_CXX_LANGUAGE_STANDARD = "c\+\+20";', 'CLANG_CXX_LANGUAGE_STANDARD = "c++23";', body)
    if new != body:
        out = out[:blk.start()] + new + out[blk.end():]
        changed += 1

print("  configurations raised to c++23: %d" % changed)
if not changed:
    sys.exit("nothing changed - refusing to write")
open(PATH, "w").write(out)
print("  WROTE", PATH)
