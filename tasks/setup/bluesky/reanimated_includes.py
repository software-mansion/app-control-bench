#!/usr/bin/env python3
"""Add the standard includes react-native-reanimated relies on transitively.

Under C++23 (which RNReanimated needs on the Xcode 26 SDK - see pods_cxx23.py) libc++ no longer
pulls <algorithm>/<iterator>/<numeric> in through other headers, so Reanimated sources that use
std::any_of, std::istream_iterator etc. stop compiling. Only ADD includes; never reorder or remove.
Idempotent.
"""
import os
import re
import sys

ROOT = os.path.expanduser("~/dev/social-app/node_modules/react-native-reanimated")
NEEDS = {
    "algorithm": r"std::(any_of|all_of|none_of|find_if|find\b|sort|transform|copy_if|remove_if|min_element|max_element|for_each|count_if)",
    "iterator":  r"std::(istream_iterator|ostream_iterator|back_inserter|front_inserter|inserter|distance|advance)",
    "numeric":   r"std::(accumulate|iota|inner_product)",
    "atomic":    r"std::atomic\b",
    "mutex":     r"std::(mutex|recursive_mutex|lock_guard|unique_lock|scoped_lock)\b",
}

patched = []
for base, _, files in os.walk(ROOT):
    for fn in files:
        if not fn.endswith((".cpp", ".h", ".mm")):
            continue
        p = os.path.join(base, fn)
        try:
            src = open(p).read()
        except (UnicodeDecodeError, OSError):
            continue
        add = [h for h, pat in NEEDS.items()
               if re.search(pat, src) and ("#include <%s>" % h) not in src]
        if not add:
            continue
        # Anchor on #include OR #import: the Objective-C++ sources use #import exclusively, and
        # anchoring on #include alone silently skips exactly the .mm files that need patching.
        m = re.search(r"^#(include|import) ", src, flags=re.M)
        if not m:
            continue
        block = "".join("#include <%s>\n" % h for h in sorted(add))
        open(p, "w").write(src[:m.start()] + block + src[m.start():])
        patched.append((os.path.relpath(p, ROOT), add))

for path, add in patched:
    print("  + %-70s %s" % (path, ", ".join("<%s>" % h for h in add)))
print("  %d file(s) patched" % len(patched))
