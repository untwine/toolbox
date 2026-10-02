# Diagnosing Windows test hangs

Use this when a Windows test hangs before normal program output or fails during
DLL loading.

## Tell a real hang apart from a slow scan

A genuine hang runs to the full `ctest --timeout` (or the job's own timeout)
and has to be cancelled; the process is still alive, and orphaned test
executables show up in the cancellation log. A Windows Defender real-time
scan of a freshly-built executable is a bounded stall — it finishes on its
own within a few minutes and the test then passes.

**The Windows Defender exclusion step is very likely never the actual fix.**
It is a tempting first guess because "antivirus scanning a new binary" sounds
exactly like what a stall looks like, but a real hang (full timeout, orphaned
process) essentially always turns out to be the DLL-load mechanism below, not
scanning. Confirm which one you're looking at before spending a cycle on the
Defender exclusion: if the stall runs to the configured timeout rather than
resolving itself after a few minutes, skip straight to the causes below
instead of reaching for `Add-MpPreference -ExclusionPath`.

## Why a missing dependency hangs instead of failing fast

On a headless CI runner, a DLL whose `DllMain`/static initialization fails to
load calls into `NtRaiseHardError` — the same mechanism that would normally
pop a blocking system dialog ("A dynamic link library (DLL) initialization
routine failed"). With nobody there to dismiss it, that call blocks forever.
The process's other loader worker threads pile up waiting on the same drain,
so the whole process looks hung, not crashed. This is the unifying mechanism
behind most of the causes below — confirm it directly (see "Read a live
hang's thread state") before assuming a different explanation.

Check these causes first:

1. a dependent DLL is not reachable — DLLs install to `lib/` by convention
   (see `packaging.md`), so the test `PATH` must include every dependency's
   `lib/` directory, including `onetbb`'s
2. a published dependency was built against a different Python DLL than the
   one actually installed on the runner (a Windows-linked C extension names
   its exact Python minor version, `python312.dll` vs `python314.dll`, at
   the binary level; no soft ABI compatibility exists there)
3. CMake discovered a different Python interpreter from the one selected by
   the workflow
4. the test environment's `PATH` does not contain every required runtime
   directory

## Check a published package's layout without live Windows access

Before reaching for live debugging, rule cause 1 out directly — this needs
no Windows runner at all, just a local Conan install:

```bash
conan list "<pkg>/<version>#*" -r <remote>
conan list "<pkg>/<version>#<revision>:*" -r <remote> --format=json
conan download "<pkg>/<version>#<revision>:<package-id>" -r <remote>
conan cache path "<pkg>/<version>#<revision>:<package-id>"
```

List the package IDs for `os: Windows` (there may be several, split by an
option such as a Python version — match the one your actual build would
resolve, not just the first one), download it, then check where the `.dll`
landed in the cache path:

```bash
find "$(conan cache path ...)" -iname "*.dll" -o -iname "*.lib"
```

`.dll` and `.lib` both under `lib/` is the expected layout. A `.dll` missing
entirely, or split into `bin/` by an install rule that disagrees with the
sibling `plugInfo.json`/resource rules, is cause 1 on that exact published
revision — no runner needed to know that much.

Use:

```text
dumpbin /dependents
```

recursively to identify missing or mismatched DLLs once you do have a build
to inspect.

Reproduce the failure through `ctest` before concluding that direct execution is
equivalent. The project may attach a test-specific environment that is absent
when launching the executable manually.

## Setting up an interactive debugging session

If live runner access is explicitly authorized, a version-pinned
Windows-capable upterm action may be used temporarily and removed after
diagnosis. `lhotari/action-upterm`, the more commonly-referenced one, does
**not** support Windows and silently no-ops there instead of failing —
confirm you're using `owenthereal/action-upterm`, the actively maintained
fork with real Windows support. Pin an exact version, not a floating major
tag: `v1.x` releases before `v1.10.0` have a known `ssh-keyscan` flakiness
bug against the default `uptermd.upterm.dev` server
(`owenthereal/action-upterm#13`, fixed by removing the `ssh-keyscan`
dependency entirely); `v1.15.0` is confirmed working.

```yaml
- name: Debug via upterm session
  if: github.event_name == 'workflow_dispatch'
  uses: owenthereal/action-upterm@v1.15.0
  with:
    limit-access-to-actor: true
    wait-timeout-minutes: 30
```

Place it as its own step immediately before the step that hangs, gated to
`workflow_dispatch` so it never opens live SSH access on an ordinary push.
It pauses the job there and prints an SSH connection string in the step's
log; once connected, run the hanging command by hand (for example `ctest
--preset conan-release -VV -j1`, dropping parallelism to isolate a single
test) and watch it hang live.

Two environment gotchas once connected:

* The session drops you into Git Bash (MSYS2), not the exact environment a
  workflow `run:` step executes in — `cmake`/`ctest`/etc. may not be on
  `PATH` even though they were in the actual job step; adjust `PATH`
  explicitly if a tool isn't found.
* MSYS2 auto-converts an argument that starts with a single `/` into a
  Windows path (`/dependents` becomes `C:\msys64\dependents`). Double the
  slash (`//dependents`) or set `MSYS_NO_PATHCONV=1` to stop it.

Remove the step once diagnosed — it isn't meant to be a standing part of CI.

## Read a live hang's thread state

Once connected and the target process is genuinely hung (not exited), check
what every thread is actually doing rather than guessing from symptoms
alone:

```powershell
Get-Process <name> | Select-Object -ExpandProperty Threads
```

For a real stack trace, attach a debugger. `cdb.exe` ships with Visual
Studio's Debugging Tools for Windows:

```text
cdb.exe -p <pid>
~*k
```

`~*k` dumps every thread's stack. All threads sitting in
`LdrpReportError`/`NtRaiseHardError` (with the main thread waiting in
`LdrpDrainWorkQueue`) is the confirmed signature of cause 1 or 2 above — a
dependency the loader could not resolve, not a slow scan and not application
logic. Use `qd` (quit-detach) to leave the process running and inspectable
again later; plain `q` terminates the target process.

Prefer crash logs and noninvasive evidence when sufficient; reach for an
invasive `cdb` attach only when the log alone doesn't answer the question.
