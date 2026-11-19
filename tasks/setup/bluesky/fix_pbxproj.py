#!/usr/bin/env python3
"""Repair the duplicated targets/phases an Expo prebuild leaves in social-app's Xcode project.

Running `expo prebuild` twice over the same `ios/` dir does not replace the generated targets - it
*appends* a second set, with quoted names ('"BlueskyClip"') and fresh UUIDs. The Bluesky app target
then depends on two targets that build the same product, and `xcodebuild -scheme Bluesky` dies in
the planning phase, before it compiles anything:

    error: Multiple commands produce '.../BlueskyClip.app'
    error: Multiple commands produce conflicting outputs   (x2, the .appex copies)

Which is why the Bluesky golden could not be rebuilt after the 2026-07-13 device wipe. This script
removes the duplicates and nothing else. It is idempotent: re-run it after any prebuild.

    cd ~/dev/social-app/ios && python3 fix_pbxproj.py [--dry-run]

The UUIDs are *derived*, never hardcoded - a prebuild mints new ones every time. The rule for which
of a colliding pair to keep: the app embeds exactly one of the two products (via a Copy Files /
Embed App Clips build file), and that is the live one. The other is dead weight that nothing
consumes, and only the dependency edge drags it into the build graph.
"""
import re
import shutil
import sys

PATH = "Bluesky.xcodeproj/project.pbxproj"
APP_TARGET = "Bluesky"


def drop_line(text, ident):
    """Delete whole lines that START with ident - list entries and one-line objects."""
    return re.sub(r"^[ \t]*" + ident + r"\b.*\n", "", text, flags=re.M)


def drop_object(text, ident):
    """Delete the `<ident> /* ... */ = { ... };` object, one-line or multi-line.

    Both forms exist and they need different patterns - conflating them corrupts the file in two
    ways that still *parse*, so neither is caught by a syntax check:

    * The comment glob must not cross newlines. Allowed to, a mere *list entry* (`<id> /* Foo */,`)
      globs forward to the next `*/ = {` and swallows whole unrelated objects.
    * A one-line object (every PBXFileReference is one) closes its brace on its own line. Matched
      with the multi-line pattern, which runs to the next line that is just `};`, it eats forward
      through everything up to the end of the *following* block - which silently deleted the app's
      Frameworks phase, dropping libPods-Bluesky.a from the link.
    """
    one_line = re.sub(r"^[ \t]*" + ident + r" /\*[^\n]*\*/ = \{[^\n]*\};\n", "", text, flags=re.M)
    if one_line != text:
        return one_line
    return re.sub(r"^[ \t]*" + ident + r" /\*[^\n]*\*/ = \{(?![^\n]*\};)[\s\S]*?\n[ \t]*\};\n", "",
                  text, flags=re.M)


def block_of(text, ident):
    m = re.search(r"^[ \t]*" + ident + r" /\*[^\n]*\*/ = \{[\s\S]*?\n[ \t]*\};", text, flags=re.M)
    return m.group(0) if m else ""


def field(block, name):
    m = re.search(r"\b" + name + r" = ([^;]+);", block)
    return m.group(1).strip().strip('"') if m else None


def id_field(block, name):
    """A reference field reads `target = 354FA8F0... /* BlueskyClip */;` - the comment is part of the
    value, so take only the leading UUID or every lookup by it silently resolves to nothing."""
    v = field(block, name)
    m = re.match(r"([0-9A-F]{24})", v) if v else None
    return m.group(1) if m else None


def listing(block, name):
    m = re.search(r"\b" + name + r" = \(([\s\S]*?)\);", block)
    if not m:
        return []
    return re.findall(r"([0-9A-F]{24})", m.group(1))


def app_block(text):
    ident = re.search(r"([0-9A-F]{24}) /\* " + APP_TARGET + r" \*/ = \{\s*\n\s*isa = PBXNativeTarget", text).group(1)
    return block_of(text, ident)


def copied_name(text, bf_id):
    """The bundle a Copy Files build file copies, by NAME - the duplicates have distinct fileRef
    UUIDs pointing at the same BlueskyClip.app, so comparing UUIDs would not see them as equal."""
    bf = re.search(r"^[ \t]*" + bf_id + r"\b.*$", text, flags=re.M)
    if not bf:
        return None, None
    ref = re.search(r"fileRef = ([0-9A-F]{24})", bf.group(0))
    if not ref:
        return None, None
    return ref.group(1), field(block_of(text, ref.group(1)) or bf.group(0), "path")


def dedupe_phases(text):
    """Drop the redundant copy phase, then the doubled entries inside the survivors.

    A second prebuild appends a whole second `Embed App Clips` phase and re-lists each .appex in
    `Copy Files`. Both show up as `Multiple commands produce conflicting outputs`.
    """
    app = app_block(text)
    seen = {}
    for phase_id in listing(app, "buildPhases"):
        phase = block_of(text, phase_id)
        if "PBXCopyFilesBuildPhase" not in phase:
            continue

        # de-duplicate the files inside this phase, by copied bundle name
        files = listing(phase, "files")
        kept, names = [], set()
        for bf_id in files:
            _, name = copied_name(text, bf_id)
            if name in names:
                print("  duplicate copy   %-24s in phase %s -> dropping" % (name, field(phase, "name")))
                continue
            names.add(name)
            kept.append(bf_id)

        # A phase that copies nothing produces no output and so cannot conflict with anything; only
        # a phase copying the same bundles to the same place as an earlier one is a real duplicate.
        key = (field(phase, "name"), field(phase, "dstSubfolderSpec"), field(phase, "dstPath"),
               frozenset(names))
        if names and key in seen:
            print("  duplicate phase  %-24s -> dropping %s" % (field(phase, "name"), phase_id))
            text = drop_object(text, phase_id)
            text = drop_line(text, phase_id)
            continue
        seen[key] = phase_id
        if len(kept) != len(files):
            body = re.search(r"(\b" + phase_id + r" /\*[^\n]*\*/ = \{[\s\S]*?files = \()([\s\S]*?)(\);)", text)
            lines = [l for l in body.group(2).strip("\n").splitlines()
                     if any(k in l for k in kept)]
            text = text[:body.start()] + body.group(1) + "\n" + "\n".join(lines) + "\n\t\t\t" + body.group(3) + text[body.end():]
    return text


def main(dry_run):
    src = open(PATH).read()
    out = dedupe_phases(src)
    app = app_block(out)

    # Which product bundles does the app actually embed? (fileRefs named by its copy/embed phases)
    # Computed *after* the phase de-dup, or a redundant phase copying the phantom's product would
    # make both sides of a collision look live.
    embedded = set()
    for phase_id in listing(app, "buildPhases"):
        phase = block_of(out, phase_id)
        if "PBXCopyFilesBuildPhase" not in phase:
            continue
        for bf_id in listing(phase, "files"):
            ref, _ = copied_name(out, bf_id)
            if ref:
                embedded.add(ref)

    # Group the app's dependency edges by the product they build; anything colliding is a duplicate.
    by_product, edges = {}, {}
    for dep_id in listing(app, "dependencies"):
        dep = block_of(out, dep_id)
        tgt_id = id_field(dep, "target")
        tgt = block_of(out, tgt_id)
        key = (field(tgt, "productType"), field(tgt, "productName"))
        edges[dep_id] = (tgt_id, id_field(dep, "targetProxy"))
        by_product.setdefault(key, []).append((dep_id, tgt_id, id_field(tgt, "productReference")))

    doomed_edges, doomed_targets, doomed_products = [], [], []
    for key, group in sorted(by_product.items()):
        if len(group) < 2:
            continue
        live = [g for g in group if g[2] in embedded]
        if len(live) != 1:
            sys.exit("%s: %d of %d products are embedded - cannot tell which target is live, "
                     "refusing to guess" % (key[1], len(live), len(group)))
        keep_target = live[0][1]
        print("  %-22s %d edges -> keeping target %s (its product is the embedded one)"
              % (key[1], len(group), keep_target))
        for dep_id, tgt_id, prod_id in group:
            if tgt_id == keep_target:
                continue
            doomed_edges.append(dep_id)
            if tgt_id not in doomed_targets:
                doomed_targets.append(tgt_id)
                doomed_products.append(prod_id)

    if not doomed_edges and out == src:
        print("  nothing to repair - no duplicate phases, and no two targets build the same product")
        return

    for dep_id in doomed_edges:
        _, proxy_id = edges[dep_id]
        for ident in (dep_id, proxy_id):
            out = drop_object(out, ident)  # object FIRST: drop_line would eat its opening line and
            out = drop_line(out, ident)    # strand the body, then unlink it from the lists
    for ident in doomed_targets + doomed_products:
        out = drop_object(out, ident)
        out = drop_line(out, ident)

    # A removed product leaves behind the build file that referenced it; drop those too.
    for prod_id in doomed_products:
        for bf in re.findall(r"^[ \t]*([0-9A-F]{24}) /\*[^\n]*\*/ = \{isa = PBXBuildFile; fileRef = "
                             + prod_id + r"\b.*$", out, flags=re.M):
            out = drop_line(out, bf)
        out = drop_line(out, prod_id)

    dangling = [i for i in doomed_edges + doomed_targets + doomed_products if i in out]
    print("  removed  : %d dependency edges, %d targets" % (len(doomed_edges), len(doomed_targets)))
    print("  dangling : %s" % (dangling or "none"))
    print("  lines    : %d -> %d" % (src.count("\n"), out.count("\n")))

    if dangling:
        sys.exit("dangling references to removed objects - refusing to write")
    if dry_run:
        print("  --dry-run, not writing")
        return

    shutil.copy(PATH, PATH + ".bak")
    open(PATH, "w").write(out)
    print("  WROTE %s (backup at %s.bak)" % (PATH, PATH))
    print("  verify: xcodebuild -list -project Bluesky.xcodeproj")


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
