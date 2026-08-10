# Viewer Right-Panel Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the viewer's 10 always-visible doctor cards with a one-line status strip and turn the freed column into four tabs: Actions, Agent, Phone, Console.

**Architecture:** Backend adds three POST endpoints (`/api/text`, `/api/open-app`, `/api/console`) and two GETs (`/api/phone`, `/api/apps`) to `viewer.py`, all origin-guarded, action POSTs under `_ACTION_LOCK`. Frontend is a restructure of `viewer.html` (single file, stdlib-served). Console dispatch is an `ast`-validated whitelist — no `eval`, no arbitrary Python.

**Tech Stack:** Python stdlib (`http.server`, `ast`, `json`), vanilla JS/CSS in `viewer.html`, pytest + requests for tests.

## Global Constraints

- No new runtime dependencies (spec; repo rule: nothing beyond `requests` and `mcp`).
- Every new POST endpoint passes through `Handler._allowed()` (it already wraps all of `do_POST` — do not add routes outside it).
- Phone-touching POSTs serialize through `_ACTION_LOCK`. STOP blocks them automatically at `wda_client._request` — do not add STOP checks in the viewer.
- `/wda/locked` is DISPLAY-ONLY. Never let any code path act on it (CLAUDE.md unlock rule).
- Phone tab is passive: GETs only, no gestures, no session creation beyond the shared-session adoption `_session_request` already does.
- Unit tests must pass without a phone (`python -m pytest tests -q`).
- `tests/test_viewer.py::test_viewer_html_has_no_duplicate_element_ids` must keep passing — every new element id must be unique.
- Pins/history live in browser localStorage only (keys `sidetap.*`) — nothing new on disk.
- Vulture pre-commit hook false-positives on cross-module functions; if it blocks a commit, verify the flagged names are used elsewhere, then `git commit --no-verify`.

**Branch:** work on `viewer-panel-redesign`, merge to `main` after Task 8's rendered proof.

```bash
cd C:/Projects/phone-claude
git checkout -b viewer-panel-redesign
```

---

### Task 1: Passive phone info — `WDAClient.battery()` + `GET /api/phone`

**Files:**
- Modify: `src/phone_harness/wda_client.py` (around line 273, next to `active_app`)
- Modify: `src/phone_harness/viewer.py` (GET routing, module globals)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `WDAClient._session_request(method, path)`, existing `is_locked()`, `active_app()`.
- Produces: `WDAClient.battery() -> dict` (raw WDA `{level: 0..1, state: int}`); `GET /api/phone` → `{"battery": dict|None, "locked": bool|None, "app": dict|None}` (each field `None` when its read fails). Task 7's frontend consumes this shape.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer.py`)

```python
def test_phone_endpoint_serves_passive_info(base_url):
    c = viewer.Handler.client
    c.battery_info = {"level": 0.78, "state": 2}
    c.locked = False
    c.app = {"bundleId": "com.apple.mobilesafari", "name": "Safari", "pid": 4242}
    r = requests.get(base_url + "/api/phone", timeout=5)
    assert r.status_code == 200
    assert r.json() == {
        "battery": {"level": 0.78, "state": 2},
        "locked": False,
        "app": {"bundleId": "com.apple.mobilesafari", "name": "Safari", "pid": 4242},
    }


def test_phone_endpoint_degrades_per_field(base_url):
    # One failing read must not blank the others (spec: strip degrades).
    c = viewer.Handler.client
    c.battery_info = None  # battery() raises
    c.locked = True
    c.app = None  # active_app() raises
    r = requests.get(base_url + "/api/phone", timeout=5)
    assert r.json() == {"battery": None, "locked": True, "app": None}
```

And extend `StubClient` (keep its existing members):

```python
    battery_info = None  # set to a dict to make battery() succeed
    locked = None        # set to a bool to make is_locked() succeed
    app = None           # set to a dict to make active_app() succeed

    def battery(self):
        if self.battery_info is None:
            raise viewer.WDAError("no phone in tests")
        return self.battery_info

    def is_locked(self):
        if self.locked is None:
            raise viewer.WDAError("no phone in tests")
        return self.locked

    def active_app(self):
        if self.app is None:
            raise viewer.WDAError("no phone in tests")
        return self.app
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_viewer.py -q -k phone_endpoint`
Expected: 2 FAIL (404 "not found" — route doesn't exist).

- [ ] **Step 3: Implement**

`wda_client.py` — add below `active_app()` and update the `is_locked` comment (it is used now; drop the `# noqa: vulture`):

```python
    def battery(self) -> dict:
        """Raw WDA battery info: {level: 0..1, state: int} (state 2 = charging)."""
        return self._session_request("GET", "/wda/batteryInfo")

    # DISPLAY-ONLY: /wda/locked can report unlocked with the passcode pad on
    # screen (a test pins that unlock() never consults it). The viewer's status
    # strip shows it; nothing may act on it.
    def is_locked(self) -> bool:
        return bool(self._request("GET", "/wda/locked"))
```

`viewer.py` — module global next to `_LAST_STATUS`:

```python
# Last good /api/phone payload, served while a gesture holds _ACTION_LOCK
# (same reasoning as _LAST_STATUS).
_LAST_PHONE: dict | None = None
```

GET route (after the `/api/status` branch):

```python
            elif path == "/api/phone":
                global _LAST_PHONE
                if _ACTION_LOCK.locked() and _LAST_PHONE is not None:
                    self._json(_LAST_PHONE)
                    return
                info: dict = {"battery": None, "locked": None, "app": None}
                try:
                    info["battery"] = self.client.battery()
                except WDAError:
                    pass
                try:
                    info["locked"] = self.client.is_locked()
                except WDAError:
                    pass
                try:
                    info["app"] = self.client.active_app()
                except WDAError:
                    pass
                _LAST_PHONE = info
                self._json(info)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_viewer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/phone_harness/wda_client.py src/phone_harness/viewer.py tests/test_viewer.py
git commit -m "Viewer: passive phone info endpoint (battery, lock display, front app)"
```

---

### Task 2: `POST /api/text`, `POST /api/open-app`, `GET /api/apps`

**Files:**
- Modify: `src/phone_harness/viewer.py` (POST + GET routing)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: `helpers.send_message(contact: str, text: str) -> dict` (returns a dict with a `"sent"` bool), `helpers.open_app(name: str) -> None`, `helpers.BUNDLE_IDS` (dict, lowercase friendly-name keys).
- Produces: `POST /api/text {to, message}` → `{"ok": bool, "result": dict}` or 400 `{"ok": false, "error": str}`; `POST /api/open-app {name}` → `{"ok": true}` / 400; `GET /api/apps` → `{"known": [str, ...]}` (sorted lowercase friendly names). Tasks 5's frontend consumes these.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer.py`)

```python
def test_text_endpoint_sends_message(base_url, monkeypatch):
    sent = {}

    def fake_send(contact, text):
        sent["args"] = (contact, text)
        return {"sent": True, "contact": contact}

    monkeypatch.setattr("phone_harness.helpers.send_message", fake_send)
    r = requests.post(
        base_url + "/api/text", json={"to": "Mom", "message": "hi"}, timeout=5
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert sent["args"] == ("Mom", "hi")


def test_text_endpoint_validates_fields(base_url):
    r = requests.post(base_url + "/api/text", json={"to": "  "}, timeout=5)
    assert r.status_code == 400
    assert "required" in r.json()["error"]


def test_text_endpoint_surfaces_helper_error(base_url, monkeypatch):
    def boom(contact, text):
        raise RuntimeError("thread not found")

    monkeypatch.setattr("phone_harness.helpers.send_message", boom)
    r = requests.post(
        base_url + "/api/text", json={"to": "Mom", "message": "hi"}, timeout=5
    )
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "thread not found"}


def test_open_app_endpoint(base_url, monkeypatch):
    opened = []
    monkeypatch.setattr("phone_harness.helpers.open_app", opened.append)
    r = requests.post(base_url + "/api/open-app", json={"name": "Safari"}, timeout=5)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert opened == ["Safari"]
    r = requests.post(base_url + "/api/open-app", json={}, timeout=5)
    assert r.status_code == 400


def test_apps_endpoint_lists_known_names(base_url):
    r = requests.get(base_url + "/api/apps", timeout=5)
    names = r.json()["known"]
    assert "settings" in names and names == sorted(names)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_viewer.py -q -k "text_endpoint or open_app or apps_endpoint"`
Expected: 5 FAIL (404).

- [ ] **Step 3: Implement** (`viewer.py`)

GET route:

```python
            elif path == "/api/apps":
                from . import helpers

                self._json({"known": sorted(helpers.BUNDLE_IDS)})
```

POST routes (before the `else: not found` branch; the lazy `from . import helpers` matches the existing `/api/unlock` pattern):

```python
            elif path == "/api/text":
                from . import helpers

                to = str(payload.get("to", "")).strip()
                message = str(payload.get("message", "")).strip()
                if not to or not message:
                    self._json({"ok": False, "error": "to and message are required"}, 400)
                    return
                try:
                    with _ACTION_LOCK:
                        result = helpers.send_message(to, message)
                except WDAError:
                    raise  # existing 502 handler
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": bool(result.get("sent")), "result": result})
            elif path == "/api/open-app":
                from . import helpers

                name = str(payload.get("name", "")).strip()
                if not name:
                    self._json({"ok": False, "error": "name is required"}, 400)
                    return
                try:
                    with _ACTION_LOCK:
                        helpers.open_app(name)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                self._json({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_viewer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/phone_harness/viewer.py tests/test_viewer.py
git commit -m "Viewer: /api/text, /api/open-app, /api/apps for the Actions tab"
```

---

### Task 3: Console endpoint — `ast`-validated whitelist dispatch

**Files:**
- Modify: `src/phone_harness/viewer.py` (module level + POST routing; add `import ast` to the imports)
- Test: `tests/test_viewer.py`

**Interfaces:**
- Consumes: helper functions by name via `getattr(helpers, name)`.
- Produces: `_CONSOLE_TOOLS: tuple[str, ...]`, `_parse_console(line: str) -> tuple[str, list, dict]` (raises `ValueError`), `POST /api/console {line}` → `{"ok": true, "result": <json, repr-fallback>}` | 400 `{"ok": false, "error": str}` (parse) | 200 `{"ok": false, "error": str}` (helper raised). Task 6's frontend consumes this.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_viewer.py`)

```python
def test_parse_console_accepts_literal_call():
    name, args, kwargs = viewer._parse_console('tap_text("General", exact=True)')
    assert (name, args, kwargs) == ("tap_text", ["General"], {"exact": True})
    assert viewer._parse_console("swipe(10, -20, 10.5, 400)")[1] == [10, -20, 10.5, 400]


@pytest.mark.parametrize(
    "line",
    [
        "os.system('calc')",          # attribute call
        "__import__('os')",           # not whitelisted
        "screenshot()",               # deliberately excluded
        "tap(1+2, 3)",                # non-literal arg
        "tap_text(open('x'))",        # call as arg
        "tap(1); tap(2)",             # not a single expression
        "ocr",                        # not a call
        "send_message(**{'contact': 'Mom'})",  # **kwargs
        "",
    ],
)
def test_parse_console_rejects(line):
    with pytest.raises(ValueError):
        viewer._parse_console(line)


def test_console_endpoint_runs_whitelisted_helper(base_url, monkeypatch):
    monkeypatch.setattr(
        "phone_harness.helpers.ocr", lambda: [{"text": "General", "x": 1, "y": 2}]
    )
    r = requests.post(base_url + "/api/console", json={"line": "ocr()"}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": [{"text": "General", "x": 1, "y": 2}]}


def test_console_endpoint_rejects_bad_line(base_url):
    r = requests.post(
        base_url + "/api/console", json={"line": "__import__('os')"}, timeout=5
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_console_endpoint_surfaces_helper_error(base_url, monkeypatch):
    def boom():
        raise RuntimeError("no text 'X' on screen")

    monkeypatch.setattr("phone_harness.helpers.ocr", boom)
    r = requests.post(base_url + "/api/console", json={"line": "ocr()"}, timeout=5)
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "no text 'X' on screen"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_viewer.py -q -k console`
Expected: FAIL (`_parse_console` doesn't exist; endpoint 404).

- [ ] **Step 3: Implement** (`viewer.py`, module level; add `import ast` up top)

```python
# Console whitelist: helper names the viewer's console may dispatch. Mirrors
# mcp_server._TOOLS (not imported: that module needs the mcp package).
# screenshot excluded — bytes don't render in a JSON console.
_CONSOLE_TOOLS = (
    "ocr",
    "screen_info",
    "tap",
    "tap_text",
    "long_press",
    "swipe",
    "scroll",
    "type_text",
    "press_home",
    "open_app",
    "current_app",
    "wait_for_app",
    "find_text",
    "wait_for_text",
    "wait_stable",
    "read_messages",
    "send_message",
    "unlock",
)


def _console_literal(node: ast.AST):
    """A literal AST node's value. Raises ValueError on anything non-literal."""
    if isinstance(node, ast.Constant):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        return -node.operand.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_console_literal(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        if any(k is None for k in node.keys):  # {**x}
            raise ValueError("arguments must be literals")
        return {
            _console_literal(k): _console_literal(v)
            for k, v in zip(node.keys, node.values)
        }
    raise ValueError("arguments must be literals")


def _parse_console(line: str) -> tuple[str, list, dict]:
    """Parse one whitelisted helper call. -> (name, args, kwargs).

    Accepts exactly `name(literal, key=literal, ...)` where name is in
    _CONSOLE_TOOLS. Everything else raises ValueError — this is the whole
    security story of /api/console, so nothing here may call eval/exec.
    """
    try:
        expr = ast.parse(line.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc.msg}") from exc
    if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Name):
        raise ValueError('one helper call, e.g. tap_text("General")')
    name = expr.func.id
    if name not in _CONSOLE_TOOLS:
        raise ValueError(f"unknown helper: {name}")
    args = [_console_literal(a) for a in expr.args]
    if any(k.arg is None for k in expr.keywords):  # **kwargs
        raise ValueError("**kwargs not allowed")
    kwargs = {k.arg: _console_literal(k.value) for k in expr.keywords}
    return name, args, kwargs
```

POST route:

```python
            elif path == "/api/console":
                from . import helpers

                try:
                    name, args, kwargs = _parse_console(str(payload.get("line", "")))
                except ValueError as exc:
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
                try:
                    with _ACTION_LOCK:
                        result = getattr(helpers, name)(*args, **kwargs)
                except WDAError:
                    raise
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)})
                    return
                body = json.dumps({"ok": True, "result": result}, default=repr)
                self._send(200, body.encode(), "application/json")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/phone_harness/viewer.py tests/test_viewer.py
git commit -m "Viewer: /api/console - ast-whitelisted helper dispatch, no eval"
```

---

### Task 4: HTML shell — status strip, tab bar, checks collapse, Agent tab

**Files:**
- Modify: `src/phone_harness/viewer.html`
- Test: `tests/test_viewer.py` (existing duplicate-id test guards this task)

**Interfaces:**
- Consumes: existing `/api/doctor` payload (`[{name, ok, detail, fix}]`), existing `loadDoctor`/`loadActivity`/`loadSent` functions.
- Produces: element ids `strip`, `strip-main`, `strip-dot`, `strip-text`, `strip-info`, `checks-wrap`, `tabs`, `tab-actions`, `tab-agent`, `tab-phone`, `tab-console`; JS `showTab(name)`, `lastDoctor` (array, set by `loadDoctor`). Tasks 5-7 fill the panes.

- [ ] **Step 1: Restructure the `#side` markup.** Replace the block from `<h1>phone-harness</h1>` through `</div>` of `#sent` (keeping `#fix-panel` intact) with:

```html
    <h1>phone-harness</h1>
    <div class="dim">Your iPhone, driven from Windows over USB.</div>
    <div id="fix-panel" hidden>
      <!-- unchanged fix-panel contents -->
    </div>
    <div id="strip">
      <button id="strip-main" title="Show / hide the full checks">
        <span id="strip-dot" class="dot"></span><span id="strip-text">Checking…</span>
      </button>
      <span id="strip-info" class="dim"></span>
    </div>
    <div id="checks-wrap" hidden>
      <div id="doctor"><div class="sec">Checks</div><div class="dim">Running checks…</div></div>
    </div>
    <nav id="tabs">
      <button class="tab" data-tab="actions">Actions</button>
      <button class="tab" data-tab="agent">Agent</button>
      <button class="tab" data-tab="phone">Phone</button>
      <button class="tab" data-tab="console">&gt;_</button>
    </nav>
    <div id="tab-actions" class="tabpane"></div>
    <div id="tab-agent" class="tabpane" hidden>
      <div id="activity" hidden>
        <h2 class="sec">Activity</h2>
        <div id="activity-list"></div>
      </div>
      <div id="sent" hidden>
        <h2 class="sec">Recent sends</h2>
        <div id="sent-list"></div>
      </div>
    </div>
    <div id="tab-phone" class="tabpane" hidden></div>
    <div id="tab-console" class="tabpane" hidden></div>
```

Notes: the old `#hint` ("Agents drive it from a terminal…") is deleted — its content reappears as the console tab's hint line in Task 6. `#activity-list` max-height changes from 220px to `55vh` (Agent tab owns the column now).

- [ ] **Step 2: Add CSS** (in the `#side` section of the stylesheet; drop the old `#hint` rule, change `#doctor`'s `margin-top` to `var(--s2)`, change `#activity-list` max-height):

```css
  #strip { display:flex; align-items:center; gap:var(--s2); padding:9px 12px; margin-top:var(--s3);
           background:var(--panel); border:1px solid var(--line); border-radius:var(--r2); }
  #strip-main { border:0; background:none; padding:0; font-size:13px; font-weight:600; gap:8px; }
  #strip-main:hover { background:none; color:var(--text); }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--ok); display:inline-block; flex:0 0 auto; }
  .dot.bad { background:var(--bad); }
  #strip-info { margin-left:auto; font-size:12.5px; white-space:nowrap; }
  #checks-wrap { margin-top:var(--s2); }
  #tabs { display:flex; gap:var(--s1); margin-top:var(--s3); border-bottom:1px solid var(--line); }
  .tab { border:0; background:none; border-radius:var(--r1) var(--r1) 0 0; padding:8px 12px; color:var(--dim); }
  .tab:hover { background:var(--panel); color:var(--text); }
  .tab.on { color:var(--text); box-shadow:inset 0 -2px 0 var(--accent); }
  .tabpane { margin-top:var(--s3); }
  #activity-list { max-height:55vh; overflow-y:auto; }
```

- [ ] **Step 3: Add tab + strip JS** (near the top of the script, after `escapeHtml`):

```js
// ---- tabs ------------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll('#tabs .tab').forEach(b =>
    b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p => p.hidden = p.id !== 'tab-' + name);
  localStorage.setItem('sidetap.tab', name);
}
document.querySelectorAll('#tabs .tab').forEach(b => b.onclick = () => showTab(b.dataset.tab));
showTab(localStorage.getItem('sidetap.tab') || 'actions');

// ---- status strip ----------------------------------------------------------
document.getElementById('strip-main').onclick = () => {
  const w = document.getElementById('checks-wrap');
  w.hidden = !w.hidden;
};
```

- [ ] **Step 4: Teach `loadDoctor` the strip.** Add `let lastDoctor = [];` next to the other state variables. Inside `loadDoctor`'s success path, after `el.innerHTML = LABEL + results.map(...)`, add:

```js
    lastDoctor = results;
    const fails = results.filter(r => !r.ok).length;
    document.getElementById('strip-dot').className = 'dot' + (fails ? ' bad' : '');
    document.getElementById('strip-text').textContent =
      fails ? fails + ' check' + (fails > 1 ? 's' : '') + ' failing' : 'All checks pass';
    if (fails) document.getElementById('checks-wrap').hidden = false;  // auto-expand
```

In the `catch` branch, also set the strip to a failure state:

```js
    document.getElementById('strip-dot').className = 'dot bad';
    document.getElementById('strip-text').textContent = 'viewer unreachable';
    document.getElementById('checks-wrap').hidden = false;
```

- [ ] **Step 5: Verify**

Run: `python -m pytest tests -q` (duplicate-id test covers the new markup).
Then `python launch.py` (or the Sidetap shortcut): strip shows "All checks pass" collapsed; clicking it expands the 10 cards; Agent tab shows Activity + Recent sends; the other tabs are empty panes. Force a FAIL (`.state/STOP` via the STOP button makes the kill-switch check fail) → strip goes red and auto-expands; RESUME clears it on the next Refresh checks.

- [ ] **Step 6: Commit**

```bash
git add src/phone_harness/viewer.html
git commit -m "Viewer: status strip + tab shell; checks collapse to one line"
```

---

### Task 5: Actions tab — text someone + open app, learned & pinnable chips

**Files:**
- Modify: `src/phone_harness/viewer.html`

**Interfaces:**
- Consumes: `POST /api/text`, `POST /api/open-app`, `GET /api/apps` (Task 2), existing `/api/actions` payload (`[{contact, resolved_title, text, sent}]`), `inputEnabled`, `showHint`, `loadSent`.
- Produces: localStorage keys `sidetap.pins.contacts`, `sidetap.pins.apps`.

- [ ] **Step 1: Markup** — fill `#tab-actions`:

```html
    <div id="tab-actions" class="tabpane">
      <div class="sec">Text someone</div>
      <div id="contact-chips" class="chips"></div>
      <input id="text-to" placeholder="Contact (as named in Messages)">
      <div id="text-row">
        <textarea id="text-msg" rows="2" placeholder="Message"></textarea>
        <button id="btn-text-send">Send</button>
      </div>
      <div class="sec gap-top">Open app</div>
      <div id="app-chips" class="chips"></div>
    </div>
```

- [ ] **Step 2: CSS**

```css
  .chips { display:flex; flex-wrap:wrap; gap:var(--s1); margin-bottom:var(--s2); }
  .chip { padding:5px 11px; border-radius:99px; font-size:12.5px; }
  .chip .pin { margin-left:6px; opacity:.5; }
  .chip.pinned .pin { opacity:1; }
  .gap-top { margin-top:var(--s4); }
  #text-to { width:100%; margin-bottom:var(--s1); }
  #text-to, #text-msg { background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:var(--r1); padding:7px 10px; font-family:inherit; font-size:13px; }
  #text-to:focus-visible, #text-msg:focus-visible, #console-in:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  #text-row { display:flex; gap:var(--s1); align-items:stretch; }
  #text-msg { flex:1; resize:vertical; min-height:38px; }
```

- [ ] **Step 3: JS** (append to the script):

```js
// ---- Actions tab -----------------------------------------------------------
function pins(key) {
  try { return JSON.parse(localStorage.getItem(key)) || []; } catch (e) { return []; }
}
function togglePin(key, name, render) {
  const p = pins(key);
  const i = p.indexOf(name);
  if (i < 0) p.push(name); else p.splice(i, 1);
  localStorage.setItem(key, JSON.stringify(p));
  render();
}
function chip(label, pinned, onUse, onPin) {
  const b = document.createElement('button');
  b.className = 'chip' + (pinned ? ' pinned' : '');
  b.textContent = label;
  const pin = document.createElement('span');
  pin.className = 'pin';
  pin.textContent = pinned ? '★' : '☆';
  pin.title = pinned ? 'Unpin' : 'Pin';
  pin.onclick = (ev) => { ev.stopPropagation(); onPin(); };
  b.appendChild(pin);
  b.onclick = onUse;
  return b;
}
let sendRecs = [];  // loadSent keeps this fresh
function renderContactChips() {
  const wrap = document.getElementById('contact-chips');
  const pinned = pins('sidetap.pins.contacts');
  const learned = [...new Set(sendRecs.slice().reverse()
    .map(r => r.resolved_title || r.contact))].filter(n => !pinned.includes(n));
  wrap.replaceChildren(...[...pinned, ...learned].slice(0, 10).map(name =>
    chip(name, pinned.includes(name),
      () => { document.getElementById('text-to').value = name;
              document.getElementById('text-msg').focus(); },
      () => togglePin('sidetap.pins.contacts', name, renderContactChips))));
}
let knownApps = [];
function renderAppChips() {
  const wrap = document.getElementById('app-chips');
  const pinned = pins('sidetap.pins.apps');
  const rest = knownApps.filter(n => !pinned.includes(n));
  wrap.replaceChildren(...[...pinned, ...rest].map(name =>
    chip(name, pinned.includes(name),
      async (ev) => {
        const b = ev.currentTarget; b.disabled = true;
        try {
          const r = await fetch('/api/open-app', {method:'POST', headers:JSON_HDR,
                                                  body: JSON.stringify({name})});
          const j = await r.json();
          if (!j.ok) showHint(j.error || 'Could not open ' + name);
        } catch (e) { showHint('Could not open ' + name); }
        b.disabled = !inputEnabled;
      },
      () => togglePin('sidetap.pins.apps', name, renderAppChips))));
  updateActionAvail();
}
getJSON('/api/apps').then(j => { knownApps = j.known; renderAppChips(); })
  .catch(() => {});
document.getElementById('btn-text-send').onclick = async () => {
  const to = document.getElementById('text-to').value.trim();
  const message = document.getElementById('text-msg').value;
  if (!to || !message.trim()) { showHint('Contact and message are both needed.'); return; }
  const btn = document.getElementById('btn-text-send');
  btn.disabled = true; btn.textContent = 'Sending…';
  try {
    const r = await fetch('/api/text', {method:'POST', headers:JSON_HDR,
                                        body: JSON.stringify({to, message})});
    const j = await r.json();
    showHint(j.ok ? 'Sent to ' + to + '.'
                  : (j.error || 'Send not confirmed — check Recent sends.'));
    if (j.ok) document.getElementById('text-msg').value = '';
  } catch (e) { showHint('Send failed.'); }
  btn.disabled = !inputEnabled; btn.textContent = 'Send';
  loadSent(); renderContactChips();
};
// Action controls need the input driver; grey them out with a reason otherwise.
function updateActionAvail() {
  const tip = inputEnabled ? '' : 'Touch input is down — see checks';
  ['btn-text-send', 'btn-console-run'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.disabled = !inputEnabled; el.title = tip; }
  });
  document.querySelectorAll('#app-chips .chip').forEach(c => {
    c.disabled = !inputEnabled; c.title = tip;
  });
}
```

- [ ] **Step 4: Wire refresh points.** In `loadSent`, after `const recs = await getJSON('/api/actions');` add `sendRecs = recs; renderContactChips();` (before the early-return on empty — chips should render from pins even with no history; move the `sendRecs`/render lines above the `if (!recs.length)` line). In `loadStatus`, after the `paste-row` visibility line, add `updateActionAvail();`.

- [ ] **Step 5: Verify rendered.** `python -m pytest tests -q`, then restart the viewer: chips appear from send history; pin one, reload the page — it stays first; Send with an empty field hints instead of posting; with the phone connected, an app chip opens the app and Send delivers a real text (watch Recent sends).

- [ ] **Step 6: Commit**

```bash
git add src/phone_harness/viewer.html
git commit -m "Viewer: Actions tab - text someone + open app with learned/pinned chips"
```

---

### Task 6: Console tab

**Files:**
- Modify: `src/phone_harness/viewer.html`

**Interfaces:**
- Consumes: `POST /api/console` (Task 3), `pins()` from Task 5, `updateActionAvail` covers `btn-console-run`.
- Produces: localStorage key `sidetap.console.hist` (array of strings, cap 50).

- [ ] **Step 1: Markup** — fill `#tab-console`:

```html
    <div id="tab-console" class="tabpane" hidden>
      <div class="sec">Console</div>
      <div id="console-row">
        <input id="console-in" placeholder='tap_text("General") — Enter runs, ↑ history' spellcheck="false">
        <button id="btn-console-run">Run</button>
      </div>
      <div id="console-out" hidden></div>
      <div class="dim" id="console-hint">
        Whitelisted helpers only: <code>ocr()</code>, <code>tap_text("…")</code>,
        <code>open_app("…")</code>, <code>swipe(x1,y1,x2,y2)</code>,
        <code>send_message("Mom","hi")</code>…
        Agents get the same helpers from a terminal: <code>phone-harness &lt;&lt;'PY' … PY</code>.
      </div>
    </div>
```

- [ ] **Step 2: CSS**

```css
  #console-row { display:flex; gap:var(--s1); }
  #console-in { flex:1; background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:var(--r1); padding:7px 10px; font-family:var(--mono); font-size:12.5px; }
  #console-out { font-family:var(--mono); font-size:12px; background:var(--panel);
    border:1px solid var(--line); border-radius:var(--r2); padding:var(--s2);
    margin-top:var(--s2); max-height:52vh; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; }
  #console-out > div { padding:4px 0; border-bottom:1px solid var(--line); }
  #console-hint { margin-top:var(--s2); line-height:1.7; }
```

- [ ] **Step 3: JS**

```js
// ---- Console tab -----------------------------------------------------------
const consoleIn = document.getElementById('console-in');
let consoleHist = pins('sidetap.console.hist');
let histIdx = consoleHist.length;
async function runConsole() {
  const line = consoleIn.value.trim();
  if (!line) return;
  consoleHist.push(line);
  if (consoleHist.length > 50) consoleHist.shift();
  localStorage.setItem('sidetap.console.hist', JSON.stringify(consoleHist));
  histIdx = consoleHist.length;
  const out = document.getElementById('console-out');
  out.hidden = false;
  const entry = document.createElement('div');
  entry.textContent = '> ' + line + '\n…';
  out.prepend(entry);
  consoleIn.value = '';
  try {
    const r = await fetch('/api/console', {method:'POST', headers:JSON_HDR,
                                           body: JSON.stringify({line})});
    const j = await r.json();
    entry.textContent = '> ' + line + '\n' +
      (j.ok ? JSON.stringify(j.result === undefined ? null : j.result, null, 1)
            : '✗ ' + j.error);
  } catch (e) { entry.textContent = '> ' + line + '\n✗ ' + e; }
}
document.getElementById('btn-console-run').onclick = runConsole;
consoleIn.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter') runConsole();
  else if (ev.key === 'ArrowUp') {
    if (histIdx > 0) { histIdx--; consoleIn.value = consoleHist[histIdx]; }
    ev.preventDefault();
  } else if (ev.key === 'ArrowDown') {
    histIdx = Math.min(histIdx + 1, consoleHist.length);
    consoleIn.value = consoleHist[histIdx] || '';
    ev.preventDefault();
  }
});
```

(The page-level keydown forwarder already ignores keys typed inside `input,textarea`, so console typing never leaks to the phone.)

- [ ] **Step 4: Verify rendered.** Restart viewer → Console tab: `ocr()` prints elements; `os.system('calc')` shows `✗ one helper call…` or `✗ unknown helper…`; up-arrow recalls; reload page → history survives.

- [ ] **Step 5: Commit**

```bash
git add src/phone_harness/viewer.html
git commit -m "Viewer: console tab - whitelisted helper one-liners with history"
```

---

### Task 7: Phone tab + strip glance info

**Files:**
- Modify: `src/phone_harness/viewer.html`

**Interfaces:**
- Consumes: `GET /api/phone` (Task 1), `lastDoctor` (Task 4), `points` (existing), `escapeHtml`.
- Produces: `loadPhone()` on a 10s interval (also feeds `#strip-info`).

- [ ] **Step 1: Markup** — fill `#tab-phone`:

```html
    <div id="tab-phone" class="tabpane" hidden>
      <div class="sec">Phone</div>
      <div id="phone-rows" class="dim">Waiting for phone…</div>
      <div class="dim gap-top">Passive reads only — nothing here touches the phone.</div>
    </div>
```

- [ ] **Step 2: CSS**

```css
  .prow { display:flex; justify-content:space-between; gap:var(--s2); padding:7px 2px;
          border-bottom:1px solid var(--line); font-size:13px; }
  .prow .k { color:var(--dim); }
```

- [ ] **Step 3: JS**

```js
// ---- Phone tab + strip glance info (passive reads only) --------------------
function appName(app) {
  return (app && (app.name || String(app.bundleId || '').split('.').pop())) || '?';
}
async function loadPhone() {
  try {
    const p = await getJSON('/api/phone');
    const bits = [];
    if (p.battery && p.battery.level != null)
      bits.push('🔋' + Math.round(p.battery.level * 100) + '%');
    if (p.locked != null) bits.push(p.locked ? '🔒' : '🔓');
    if (p.app) bits.push(appName(p.app));
    document.getElementById('strip-info').textContent = bits.join('  ');
    const sig = lastDoctor.find(r => r.name === 'input signature (7-day)');
    const rows = [
      ['Battery', p.battery && p.battery.level != null
        ? Math.round(p.battery.level * 100) + '%' + (p.battery.state === 2 ? ' · charging' : '')
        : '—'],
      ['Lock state', p.locked == null ? '—'
        : (p.locked ? 'Locked' : 'Unlocked') + ' (display-only)'],
      ['Front app', p.app ? appName(p.app) + ' · ' + (p.app.bundleId || '') : '—'],
      ['Screen', points ? points.width + ' × ' + points.height + ' pt' : '—'],
      ['Input signature', sig ? sig.detail : '—'],
    ];
    document.getElementById('phone-rows').innerHTML = rows.map(([k, v]) =>
      `<div class="prow"><span class="k">${k}</span><span>${escapeHtml(v)}</span></div>`).join('');
  } catch (e) { /* passive info is best-effort */ }
}
```

- [ ] **Step 4: Schedule it.** In the `window load` handler add `loadPhone();` and `setInterval(loadPhone, 10000);`.

- [ ] **Step 5: Verify rendered.** Restart viewer: strip right side shows battery/lock/app within 10s; Phone tab rows fill; unplug WDA (or before signing) → rows show `—`, strip shows doctor state alone.

- [ ] **Step 6: Commit**

```bash
git add src/phone_harness/viewer.html
git commit -m "Viewer: phone tab + strip glance info (passive reads only)"
```

---

### Task 8: Docs, full verification, merge

**Files:**
- Modify: `README.md` (viewer feature list), `CLAUDE.md` (viewer.py bullet)
- No code changes.

- [ ] **Step 1: Update docs.** In `CLAUDE.md`, extend the `viewer.py` bullet's feature list: replace "doctor panel, live Activity feed, Recent sends audit list" with "one-line status strip (checks collapse; auto-expands on FAIL), tabs: Actions (text someone / open app, learned+pinned chips), Agent (Activity feed + Recent sends), Phone (passive info only), Console (ast-whitelisted helper one-liners via /api/console — no eval)". In `README.md`, find the viewer description and add one sentence: "The side panel is a dashboard: quick actions (text someone, open an app), the agent activity feed, passive phone info, and a console for helper one-liners."

- [ ] **Step 2: Full test run.** `python -m pytest tests -q` — expect all green.

- [ ] **Step 3: Rendered proof (Definition of Done).** Restart the viewer through the Sidetap shortcut and walk every surface: strip collapsed green → click expands → Refresh checks; Actions: pin a contact, send a real text, open a real app; Agent: feeds render; Phone: rows filled; Console: `ocr()` and a rejected `__import__('os')`; STOP → strip red + auto-expand → RESUME.

- [ ] **Step 4: Merge and push.**

```bash
git checkout main
git merge --no-ff viewer-panel-redesign -m "Viewer: right panel becomes a dashboard (strip + tabs)"
git push
git branch -d viewer-panel-redesign
```

---

## Self-review notes

- Spec coverage: strip (T1/T4/T7), tabs (T4), Actions (T2/T5), Agent (T4), Phone (T1/T7), Console (T3/T6), error handling (T2/T3 tests + per-field degrade T1), testing + rendered proof (every task step 4/5, T8). Non-goals respected: no message pulling anywhere; console has no eval path; pins/history in localStorage only.
- Deviation from spec recorded: app chips come from `helpers.BUNDLE_IDS` known names + pins, not from activity-log parsing — the activity log records bundle ids (`open app: com.apple.…`), which make ugly chips; the known-names list is the same data the helper actually accepts. Contacts are learned from history as specced.
- Type consistency: `/api/phone` shape used identically in T1 tests and T7 JS; `_parse_console` tuple shape matches T3 endpoint use; `pins()` defined once (T5) and reused (T6).
