#!/usr/bin/env python3
import json, urllib.request, urllib.error, time
HS = "http://localhost:8008"
def api(m, p, t=None, b=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(HS + p, data=d, method=m)
    r.add_header("Content-Type", "application/json")
    if t: r.add_header("Authorization", "Bearer " + t)
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

st = res = None
for attempt in range(6):
    st, res = api("POST", "/_matrix/client/v3/login", b={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": "alice"},
        "password": "alice-bench-2026"})
    if st == 200:
        break
    print("login retry", st, res.get("errcode"), res.get("error"))
    time.sleep(res.get("retry_after_ms", 3000) / 1000 if isinstance(res, dict) else 3)
if st != 200:
    raise SystemExit(f"login failed: {st} {res}")
tok, uid = res["access_token"], res["user_id"]
print("LOGIN OK as", uid)
st, jr = api("GET", "/_matrix/client/v3/joined_rooms", tok)
rooms = jr["joined_rooms"]
print("alice joined_rooms:", len(rooms))
st, md = api("GET", f"/_matrix/client/v3/user/{uid}/account_data/m.direct", tok)
print("m.direct:", md)
for rid in rooms:
    st, nm = api("GET", f"/_matrix/client/v3/rooms/{rid}/state/m.room.name/", tok)
    name = nm.get("name", "(dm/no-name)")
    st, jm = api("GET", f"/_matrix/client/v3/rooms/{rid}/joined_members", tok)
    n = len(jm.get("joined", {})) if st == 200 else "?"
    st, tg = api("GET", f"/_matrix/client/v3/user/{uid}/rooms/{rid}/tags", tok)
    tags = ",".join(tg.get("tags", {}).keys()) if st == 200 else ""
    st, rt = api("GET", f"/_matrix/client/v3/rooms/{rid}/state/m.room.create/", tok)
    rtype = rt.get("type", "")
    print(f"  {name:16} members={n}  type={rtype or 'room'}  tags={tags}")
