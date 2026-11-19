# Expensify

**Tasks:** `expe-01` … `expe-30` (30) · **Bundle:** _not yet captured_ · **Status:** ❌ **notes only - not
set up, not frozen, not wired**

The 30 tasks exist in `tasks/tasks.json`, but the surface has never been built. When the decision was made to
freeze exactly one surface properly, that surface was [Immich](immich.md). This page is what we know, not a
working runbook.

## What is known

- **Source:** `~/dev/expensify-App` on the bench host.
- **Build:** `npm run ios` - a native build, ~5-10 minutes. It connects to the **production** API. (The v1
  "offline" problem was a *web*-only issue and does not apply to the native build.)
- **Bundle id:** discover it after installing -
  `xcrun simctl listapps booted | /usr/bin/grep -i expensify` - then add it to `runner/bench.py` `APPS`,
  where the entry is currently **commented out** (~line 55):

  ```python
  "expensify": {"bundle": "<id>", "open": None},
  ```

- **Auth:** the logged-in tasks need a real Expensify account. Expensify is **passwordless** - email plus a
  6-digit code, no phone - so seeding an account needs an inbox (a temp-email API such as mail.tm worked in
  v1), or a one-time manual login whose session then persists.

## What it would take to freeze it

Follow the [Immich template](immich.md):

1. Pin the app build (it currently tracks whatever `~/dev/expensify-App` is checked out at).
2. Script the account seeding - this is the hard part, because it talks to **production**. A self-hosted
   backend does not exist, which means Expensify cannot get the deterministic per-run reset every other
   surface has. It would need a sweep-style `reset_post` like the old
   [Bluesky `cleanup.py`](bluesky.md#reset).
3. Capture the bundle id.
4. Build a task → data coverage matrix for all 30 tasks.
5. Add `bring-up.sh` / `verify.sh` / `reset.sh`.
6. Install and log in **in the golden**.

**The honest blocker:** running a benchmark that mutates a production SaaS account is a materially worse
proposition than the other four surfaces, all of which are self-hosted and resettable. That is why this one
keeps losing the prioritisation argument.
