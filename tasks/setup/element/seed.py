#!/usr/bin/env python3
"""Deterministic, reseedable Synapse seed for the 30 element-* benchmark tasks.

Creates the scenario the tasks assume:
  - primary app user @alice (known password)  -> log the Element app in as this user
  - @bob with a real 1:1 DM (m.direct)         -> element-02 / element-29 / People filter
  - @carol, @dave as extra members             -> Project Phoenix member list, timelines
  - rooms: Team Standup, Weekend Plans, Book Club, Project Phoenix
      * Team Standup has a message containing "update" (element-22 search) + msgs to react to
      * Book Club has messages to read/reply to
  - Team Standup tagged m.favourite            -> Favourites filter (element-11)
  - a Space "Benchmark HQ"                      -> Spaces view (element-21)
  - unread messages (last event by a non-alice) -> Unreads filter (element-13)

Idempotent: purges all existing rooms first, recreates the exact set. Users persist.
Run on radoniusz:  python3 element_seed.py
"""
import json, subprocess, sys, time, urllib.request, urllib.error

HS = "http://localhost:8008"
DOMAIN = "localhost"
REG_BIN = "/Users/radoniusz/synapse-venv/bin/register_new_matrix_user"
SHARED_SECRET = "Ly1FF_AboYQJ30J:T63ULxx@&GnN02W2iO@s&whk5wCR9jt~Y,"

# passwords (known, used for app login + API)
PW = {
    "alice": "alice-bench-2026",
    "bob": "bob-bench-2026",
    "carol": "carol-bench-2026",
    "dave": "dave-bench-2026",
    "seedadmin": "seedadmin-bench-2026",
}
DISPLAY = {"alice": "Alice", "bob": "Bob", "carol": "Carol", "dave": "Dave"}

_txn = 0
def mxid(u): return f"@{u}:{DOMAIN}"

def api(method, path, token=None, body=None, _tries=6):
    # Per-run reseed hammers Synapse's rate limiter (createRoom/message limits): a full pass
    # reseeds once per run, so alice hits 429 M_LIMIT_EXCEEDED after a few rapid runs. Honor the
    # server's retry_after_ms and retry (bounded) so the seed is resilient regardless of server
    # rate-limit config. (Raise the limits server-side too for speed — see tasks/setup/element/README.)
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(_tries):
        req = urllib.request.Request(HS + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token: req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try: code, res = e.code, json.loads(e.read().decode() or "{}")
            except Exception: code, res = e.code, {}
            if code == 429 and attempt < _tries - 1:
                wait = min(res.get("retry_after_ms", 1000) / 1000.0 + 0.5, 65)
                print(f"  429 rate-limited on {path}; waiting {wait:.1f}s (retry {attempt+1})", flush=True)
                time.sleep(wait)
                continue
            return code, res

def register(user, admin=False):
    cmd = [REG_BIN, "-u", user, "-p", PW[user],
           ("-a" if admin else "--no-admin"), "-k", SHARED_SECRET, HS]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 0:
        print(f"  + registered {user}{' (admin)' if admin else ''}")
    elif "taken" in out.lower() or "already" in out.lower():
        print(f"  = {user} already exists")
    else:
        print(f"  ! register {user} FAILED: {out[:200]}")

def login(user):
    st, res = api("POST", "/_matrix/client/v3/login", body={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": PW[user]})
    if st != 200:
        sys.exit(f"login {user} failed: {st} {res}")
    return res["access_token"], res["user_id"]

def main():
    print("== 1. users ==")
    register("seedadmin", admin=True)
    for u in ("bob", "carol", "dave"):
        register(u)
    admin_tok, _ = login("seedadmin")

    # ensure alice exists + known password
    register("alice")  # no-op if exists
    # logout_devices MUST be False: a reseed should NOT invalidate the Element app's existing session
    # (otherwise every reseed forces a manual re-login). The app re-syncs the new rooms on its own.
    st, res = api("POST", f"/_synapse/admin/v1/reset_password/{mxid('alice')}",
                  token=admin_tok, body={"new_password": PW["alice"], "logout_devices": False})
    print(f"  reset alice password: {st}")

    toks = {}
    for u in ("alice", "bob", "carol", "dave"):
        t, uid = login(u)
        toks[u] = (t, uid)
        api("PUT", f"/_matrix/client/v3/profile/{uid}/displayname",
            token=t, body={"displayname": DISPLAY[u]})

    def tok(u): return toks[u][0]
    def uid(u): return toks[u][1]

    def send(u, rid, text):
        global _txn
        _txn += 1
        st, res = api("PUT",
                      f"/_matrix/client/v3/rooms/{rid}/send/m.room.message/seed{_txn}",
                      token=tok(u), body={"msgtype": "m.text", "body": text})
        if st != 200: print(f"    ! send by {u} failed {st} {res}")
        time.sleep(0.05)

    def join(u, rid):
        api("POST", f"/_matrix/client/v3/rooms/{rid}/join", token=tok(u))

    def create(name, invite, topic=None, direct=False, space=False):
        body = {"preset": "trusted_private_chat" if direct else "private_chat",
                "invite": [mxid(u) for u in invite]}
        if name: body["name"] = name
        if topic: body["topic"] = topic
        if direct: body["is_direct"] = True
        if space: body["creation_content"] = {"type": "m.space"}
        st, res = api("POST", "/_matrix/client/v3/createRoom", token=tok("alice"), body=body)
        if st != 200: sys.exit(f"createRoom {name} failed {st} {res}")
        return res["room_id"]

    print("== 2. purge existing rooms ==")
    st, res = api("GET", "/_synapse/admin/v1/rooms?limit=1000", token=admin_tok)
    old = res.get("rooms", [])
    print(f"  {len(old)} existing rooms")
    for r in old:
        api("DELETE", f"/_synapse/admin/v2/rooms/{r['room_id']}",
            token=admin_tok, body={"purge": True, "block": False})
        print(f"  - deleting {r.get('name') or r['room_id']}")
    if old: time.sleep(4)

    print("== 3. rooms + messages ==")
    ts = create("Team Standup", ["bob", "carol", "dave"], topic="Daily standup sync")
    for u in ("bob", "carol", "dave"): join(u, ts)
    send("alice", ts, "Morning all!")
    send("bob", ts, "Daily update: I finished the login flow.")
    send("carol", ts, "Working on the dashboard update today.")
    send("dave", ts, "No blockers from me.")
    print(f"  Team Standup {ts}")

    bc = create("Book Club", ["carol", "dave"], topic="What we're reading this month")
    for u in ("carol", "dave"): join(u, bc)
    send("alice", bc, "Welcome to Book Club!")
    send("carol", bc, "This month we're reading Dune.")
    send("dave", bc, "Loving it so far, the worldbuilding is great.")
    print(f"  Book Club {bc}")

    wp = create("Weekend Plans", ["bob"], topic="Plans for the weekend")
    join("bob", wp)
    send("bob", wp, "Anyone up for hiking Saturday?")
    print(f"  Weekend Plans {wp}")

    pp = create("Project Phoenix", ["bob", "carol", "dave"], topic="Phoenix launch coordination")
    for u in ("bob", "carol", "dave"): join(u, pp)
    send("alice", pp, "Phoenix kickoff - let's go!")
    send("carol", pp, "Design assets are ready.")
    print(f"  Project Phoenix {pp}")

    print("== 4. DM with Bob ==")
    dm = create(None, ["bob"], direct=True)
    join("bob", dm)
    send("bob", dm, "Hey Alice!")
    api("PUT", f"/_matrix/client/v3/user/{uid('alice')}/account_data/m.direct",
        token=tok("alice"), body={mxid("bob"): [dm]})
    api("PUT", f"/_matrix/client/v3/user/{uid('bob')}/account_data/m.direct",
        token=tok("bob"), body={mxid("alice"): [dm]})
    print(f"  DM(alice,bob) {dm}")

    print("== 5. favourite + space ==")
    api("PUT", f"/_matrix/client/v3/user/{uid('alice')}/rooms/{ts}/tags/m.favourite",
        token=tok("alice"), body={"order": 0.5})
    space = create("Benchmark HQ", [], space=True)
    for rid in (ts, bc, wp, pp):
        api("PUT", f"/_matrix/client/v3/rooms/{space}/state/m.space.child/{rid}",
            token=tok("alice"), body={"via": [DOMAIN]})
    print(f"  favourite=Team Standup  space=Benchmark HQ {space}")

    print("\nSEED COMPLETE. App login -> user: alice   password: " + PW["alice"] +
          "   homeserver: " + HS)

if __name__ == "__main__":
    main()
