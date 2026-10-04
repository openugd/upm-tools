# upm-tools

[![CI](https://github.com/openugd/upm-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/openugd/upm-tools/actions/workflows/ci.yml)

Regression tooling for the OpenUGD package family (`com.openugd.*`). It checks the packages at two levels.
Level 1 needs no Unity licence; run it on every commit. CI runs it on every push to `main` and every pull
request in this repository, every Monday and on demand (see [CI](#ci)). Level 2 starts a real Unity editor
before a release. The tooling lives here, not in any package repo, because it checks all the packages side by side.

| Script | When to run it | Needs | Typical time |
| --- | --- | --- | --- |
| `level1.sh` | every commit, every package | .NET SDK, an installed editor (its files only) | 25-30 s |
| `linker-gate.sh` | changes to `com.openugd.context` registration or `[Inject]`, to corelib's commands or presenter factory; before a release | an installed editor (its files only) | 20 s per editor |
| `level2.sh` | before a release, or after `.meta`/asmdef/package.json changes | Unity with an activated licence | about 1 min for three packages (measured from an empty Library) |
| `level2.sh --tarball` | before tagging: installs each package as OpenUPM would publish it | Unity with an activated licence; npm (optional) | packing 6 packages: about 40 s, then as above |
| `il2cpp-smoke.sh` | before a release; after changes to registration, `[Inject]`, commands, presenters or `SignalBase` | Unity with an activated licence and its WebGL module; Chrome for the headless check | about 3 min from an empty Library |
| `release-check.sh` | right before tagging, per package | git | 1-2 s |
| `finish.sh` | maintainer only: fast-forwards release branches in the maintainer's local repositories (see [below](#finishsh-landing-the-work-maintainer-only)); not needed to check the packages | git | instant |

Every script prints its options with `--help`. None of them pushes, tags, adds a remote or rewrites history.

## Quick start

You do not need this repository to use the packages: install them from OpenUPM as each package's README
describes (see <https://github.com/openugd>). This repository is for checking changes to the packages.

To run level 1 on macOS without the folder layout described under [Requirements](#requirements), clone the
package repos next to this one and point the tools at them (Python 3.9 or newer and the .NET SDK 10 are
needed; see Requirements):

```sh
python3 ci/clone-packages.py --dest ../packages      # one shallow clone per package repo, default branches
OPENUGD_ROOT=../packages ./level1.sh
```

`clone-packages.py --ref 2.0.0` checks out the `2.0.0` tag of each of the six packages released together as
2.0.0 instead of its default branch; `upm-configuration`, which is not part of that release, stays on its
default branch (`main`).

On a machine without Unity 6000.0.41f1 installed through Unity Hub, fetch the editor files level 1 reads first
(it streams the 5 GB installer and keeps about 2.3 GB; nothing is installed and no licence is needed), and tell
the tools where they are:

```sh
python3 ci/fetch-editor.py --version 6000.0.41f1 --changeset 46e447368a18 --dest ../unity-editors
OPENUGD_UNITY_EDITORS=../unity-editors OPENUGD_ROOT=../packages ./level1.sh
```

Both variables are relative to the current directory. This is what CI does (see [CI](#ci)).

## Requirements

- macOS with Unity editors installed through Unity Hub under `/Applications/Unity/Hub/Editor/`
  (`$OPENUGD_UNITY_EDITORS` replaces that folder, as CI does). `6000.0.41f1` is the default. `6000.3.3f1` is also supported. Both install layouts are handled: 6000.0
  keeps the scripting files in `Unity.app/Contents`, 6000.3 in `Unity.app/Contents/Resources/Scripting`.
- Python 3.9 or newer (the system `python3` is enough; no third-party modules).
- .NET SDK 10 (`dotnet`) for level 1, its metadata reader (`asmrefs/`) and the linker gate's inspector. The
  test projects restore NUnit
  3.14.0, NUnit3TestAdapter 4.6.0 and Microsoft.NET.Test.Sdk 17.11.1 from NuGet (or the local NuGet cache).
- `level2.sh` only: a Unity licence already activated in Unity Hub (Personal is fine), because it starts
  the editor in batchmode. `--tarball` packs with `npm` when it is on the `PATH` (npm 7 or newer; tested with
  10.8); without it the tarball is written directly and npm's ignore rules are not applied (the report says so).

- `il2cpp-smoke.sh` only: the same licence, the editor's WebGL module (`PlaybackEngines/WebGLSupport`), and Google
  Chrome (or Chromium/Edge) for the headless check; without a browser, `--no-check` builds and serves only.

The package checkouts are found under `config/family.json` → `root`, one folder per repo (`upm-lifetime`,
`upm-signal`, `upm-context`, `upm-corelib`, `upm-corelib-widgets`, `upm-ui`). Override it with `--root` or
`OPENUGD_ROOT` (relative to the current directory). These six are the packages released together as 2.0.0,
called "the train" below and in `config/family.json`. `upm-configuration` (`com.openugd.configuration` 0.x) is
not part of it; level 1 checks it when `--packages` names it.

Paths in `config/family.json` (`root`, `smokeProject`, `il2cppSmokeProject`) may be absolute, start with `~`, or
be relative; a relative one is resolved against the upm-tools folder, not the current directory, so the scripts
behave the same from anywhere. The committed defaults are the maintainer's layout, in which the package
checkouts sit under `Assets/` of a local Unity project (`OpenUGD/`, not published):

```
<workspace>/
  OpenUGD/Assets/upm-*     root: ../../../OpenUGD/Assets
  v2/repos/upm-tools/      this repository
  v2/smoke/                smokeProject: ../../smoke
  v2/il2cpp-smoke/         il2cppSmokeProject: ../../il2cpp-smoke
```

With another layout, edit those three values or pass `--root`, `--smoke` and `--project`.

Build output goes to `--out`, else `$OPENUGD_HARNESS_OUT` (both relative to the current directory), else `out/` in
the upm-tools folder (ignored by git). Give every concurrent run its own output folder.

## level1.sh: the per-commit gate

```sh
./level1.sh                                     # everything, all six packages
./level1.sh --packages upm-signal               # one package (its family dependencies are added)
./level1.sh --steps build,tests                 # a subset of steps
./level1.sh --unity 6000.3.3f1                  # against the other editor's DLLs and package map
```

It needs no licence and never starts the editor. It reads the editor's DLLs, its `netstandard.dll`, the
uGUI/TextMeshPro sources the editor ships (`BuiltInPackages/com.unity.ugui`), the Test Framework DLLs from
the editor's project-template cache, Unity's custom NUnit (`com.unity.ext.nunit`), and, for the `deps`
step, the module list `Resources/modules.asset` and the manifests of the Unity packages the editor ships.
That is all it reads from the editor.

### How the projects are generated

The gate generates one SDK-style project per (asmdef, variant) and builds them in dependency order, with
independent projects in parallel. It copies Unity's rules. The first two exist because leaving them out
gave wrong results in an earlier harness:

- **Declared references only.** Every project sets `DisableTransitiveProjectReferences`. Without it,
  widgets once "compiled" because it saw context through DI.
- **Implicit uGUI.** Unity adds `UnityEngine.UI` to every asmdef without `noEngineReferences`. When it
  compiles for the editor it also adds `UnityEditor.UI`. Without that rule, corelib reports dozens of false
  `CS0246` errors. This was confirmed against Unity's own compiler response files
  (`Library/Bee/artifacts/*.rsp`).
- **Engine references.** Engine module DLLs are referenced unless `noEngineReferences` is set. Editor
  modules are referenced only in the editor variant.
- **Compiler settings.** Unity's `netstandard.dll` and compat shims, C# 9, and
  `/nowarn:0169,0649,0282,1701,1702`, as in Unity's response files.
- **Two variants.**
  - *editor*: `UNITY_EDITOR`, `DEBUG`, `UNITY_INCLUDE_TESTS`, unoptimised.
  - *player*: a release IL2CPP standalone build, with `ENABLE_IL2CPP`, optimised, and without
    `UNITY_EDITOR`/`DEBUG`.

  Editor-only asmdefs exist only in the editor variant. A reference to an assembly that is not compiled
  for a variant is dropped, as Unity drops it.
- **uGUI and TextMeshPro.** Both are compiled from the editor's own package source, in both variants. The
  player variant therefore sees the player build of `UnityEngine.UI`, without its `#if UNITY_EDITOR`
  members.

### versionDefines and defineConstraints

Unity evaluates `versionDefines` against the packages installed in the project. Level 1 evaluates them
against a package-version map, `config/unity/<editor version>.json`.
- The default map, `6000.0.41f1.json`, was taken from the `Packages/manifest.json` and `packages-lock.json`
  of the maintainer's local Unity 6000.0.41f1 project. It lists `com.unity.ugui` 2.0.0,
  `com.unity.test-framework` 1.4.6, `com.unity.ext.nunit` 2.0.5 and the 32 built-in modules listed in that
  manifest, not the modules those pull in as dependencies (such as `com.unity.modules.subsystems`).
- The editor itself is entered under the name `Unity`.
- The family's own `package.json` versions are added at run time.

The symbols a `defineConstraints` entry can see are the global defines plus the assembly's own
`versionDefines`. An assembly whose constraints are unmet is not compiled. It is listed as `SKIP` with the
reason. Range syntax follows the Unity manual: `"1.2.3"`, `"[1.0,2.0)"`, `"(1.0,2.0]"`, `"[1.2.3]"`, and an
empty expression meaning "any version".

To check another project configuration, override map entries:

```sh
./level1.sh --package-version com.unity.ugui=                 # a project without uGUI
./level1.sh --package-version com.unity.textmeshpro=3.0.9      # add a package
```

`config/unity/6000.3.3f1.json` takes ugui, test-framework and ext.nunit from that editor's built-in
packages. It has not been compared against a freshly created 6000.3 project.

### Steps

| Step | What fails it |
| --- | --- |
| `build` | A compile error in any runtime, editor or test asmdef, in either variant. A documentation diagnostic in a runtime or editor assembly: `CS1591`, `CS1570`, `CS1572`, `CS1573`, `CS1574`, `CS1580`, `CS1584`, `CS1734`. Other warnings are reported, not failed. |
| `samples` | A compile error in any `Samples~` asmdef, both variants, declared references only. |
| `canary` | A canary that does not behave as `canary/<case>/expect.json` says (below). |
| `readme` | A C# fence in a package `README.md` that declares a type and does not compile (below). |
| `tests` | A failing test, a test project that does not build, a passed count below the suite's floor, or a suite with no floor. |
| `meta` | Any `.meta` problem in a package root (below). |
| `deps` | An asmdef that references an assembly of another family or Unity package that its `package.json` does not declare. Test asmdefs and asmdefs with `defineConstraints` (optional assemblies such as a TMP sub-assembly) are exempt. A compiled assembly that uses an engine module (`com.unity.modules.*`) or a uGUI/TextMeshPro assembly its `package.json` does not guarantee (see [Built-in modules](#built-in-modules-the-deps-step)). |

**How documentation errors are produced.** Documentation diagnostics are compiled as warnings, so the
assembly is still produced and its dependents still compile. The gate then promotes them to errors and
fails the assembly. A missing `<summary>` in corelib therefore cannot hide a real compile error in
widgets. Samples, tests and canaries get `CS1591` suppressed. Their other documentation warnings are
reported but do not fail the gate. README snippets are compiled without documentation output.

**Test asmdefs** are compiled twice:
- **Faithfully**, as above: netstandard2.1 against Unity's NUnit 3.5 and the Test Framework DLLs, with
  declared references only. This catches tests that use APIs missing from Unity's .NET Standard 2.1
  profile or from Unity's NUnit.
- **As a net10.0 test project** for `dotnet test --filter "TestCategory!=RequiresUnity"`. A test that
  needs the real engine (GameObject, AssetDatabase, MonoBehaviour, anything that calls into native code)
  must carry `[Category("RequiresUnity")]`. Level 1 skips it and level 2 runs it. CoreCLR is not Mono, so
  level 2 remains the authority on runtime behaviour.

**Floors.** `config/test-floors.json` holds a minimum passed count per test asmdef.
- Raise a floor in the commit that adds tests. `--raise-floors` writes the new passed counts after a run,
  and it only ever raises.
- Lower a floor only in a commit that deliberately deletes tests.
- A suite missing from the file fails the step.

### Built-in modules (the deps step)

Level 2 caught `com.openugd.corelib` using `UnityEngine.AudioListener` without declaring
`com.unity.modules.audio`. In a project without the Audio module, Unity fails to compile it with CS1069. The
build step cannot see this, because it references every engine module DLL. So the `deps` step also reads
what each compiled assembly references in its metadata, and checks that against `package.json`. The reader is
`asmrefs/`, a small System.Reflection.Metadata tool that the step builds in the output folder. It lists the
assembly's `AssemblyRef` rows and the types used from each one.

**What is checked.** Every runtime, Editor-only and `Samples~` assembly of the selected packages, in both
variants. With `deps` in the steps they are compiled even when `build` and `samples` are not.
- A reference to `UnityEngine.<Name>Module` needs the package that controls the module. Two cases need
  nothing: a module that every project has, and any use from an Editor-only assembly.
- A reference to an assembly of a Unity package that level 1 compiles from source needs that package, from
  Editor-only assemblies too. Today that is `com.unity.ugui`: `UnityEngine.UI`, `UnityEditor.UI`,
  `Unity.TextMeshPro` and `Unity.TextMeshPro.Editor`. Unity adds `UnityEngine.UI` to every asmdef without
  `noEngineReferences`, but only in a project that has uGUI. A reference that the asmdef rule above has
  already reported is not repeated.
- `UnityEditor.*Module` references never need a declaration.
- Test asmdefs are exempt, as for asmdef references.
- An assembly that did not compile cannot be read. It is listed as `NOT CHECKED`. It fails the step only
  when the `build` step (or `samples`, for a sample) is not part of the run to report it.

**What counts as declared.**
- A dependency in `package.json`.
- Anything a declared Unity package depends on, transitively, according to the manifests the editor ships.
  For example, `com.unity.ugui` brings `com.unity.modules.ui` and `com.unity.modules.imgui`.
- For an assembly whose `defineConstraints` require a `versionDefines` symbol: that package, because Unity
  compiles the assembly only when the package is present.

Family dependencies are not walked: declare what you use, as for asmdef references. A finding names the
family package that would bring the module anyway. A `versionDefines` entry for the package that the
constraints do not require is accepted with a note. Level 1 compiles with every module present, so it
cannot see whether the use sits inside the `#if`.

**Where the map comes from.** It is read from the selected editor on every run, so it follows `--unity`.
- `Contents/Resources/modules.asset` lists every engine module with a `controlledByBuiltinPackage` flag.
  `0` means that no package controls the module and Unity always references it. `1` means that the module
  belongs to the built-in package `com.unity.modules.<name in lower case>`. The step checks two things
  instead of assuming them: that each such package exists in `BuiltInPackages`, and that every
  `UnityEngine.*Module.dll` has an entry. A mismatch is a tooling problem. Modules that every project has:
  37 of 73 on 6000.0.41f1, 42 of 81 on 6000.3.3f1.
- Unity package manifests come from `Contents/Resources/PackageManager/BuiltInPackages/*/package.json`. For a
  package that is not built in (`com.unity.test-framework` on 6000.0), they come from
  `Contents/Resources/PackageManager/Editor/<name>-<version>.tgz`. If the editor ships no manifest for a
  declared Unity package, the step prints a note and does not count the modules that package would bring.

**How the rules were confirmed** (6000.0.41f1). The evidence is Unity's own compiler response files
(`Library/Bee/artifacts/*/<assembly>.rsp`) from the level-2 smoke project. Its manifest has only uGUI and the
Test Framework, so its only module packages are ui, imgui and jsonserialize.
- The runtime asmdefs `com.openugd.corelib` and `UnityEngine.UI` reference exactly the 37 modules flagged
  `0`, plus UI, IMGUI and JSONSerialize. `UnityEngine.AudioModule.dll` is not among them, and that is the
  CS1069.
- The Editor-only asmdefs `com.openugd.corelib.editor`, `com.openugd.corelib.tests` and `UnityEditor.UI`
  reference all 73 engine modules, Audio included.
- All of them reference all 43 `UnityEditor.*Module.dll` files, including those whose engine modules are
  not installed (Physics, Terrain, Video and others). `editor_modules.asset` (6000.0) has no
  `controlledByBuiltinPackage` field, and 6000.3 ships no such file.

The rules have not been compared against 6000.3.3f1's response files.

**Limits.** The check sees only what the compiler wrote into the assembly. Some uses leave no reference in
metadata and still fail in Unity: `nameof(AudioListener)`, an inlined `const`, or an overload candidate that
the compiler had to inspect but did not pick. Level 2 remains the authority. Its smoke project installs no
module package beyond those uGUI and the Test Framework bring, which is how the AudioListener use was caught.

`tests/fixtures/audio-listener` reproduces the corelib case: a runtime assembly that uses `AudioListener`,
`Canvas` (covered through `com.unity.ugui`) and `GameObject`, and an Editor-only assembly that uses
`AudioSource`. `tests/test_modules.py` runs level 1 on a copy of it. The run must fail with exactly one
finding, and must pass once `com.unity.modules.audio` is added to the copy's `package.json`.

### The canary

`canary/` holds three asmdefs that the gate builds next to the family:

| Case | References | Must | Proves |
| --- | --- | --- | --- |
| `transitive` | `com.openugd.signal` only, uses `OpenUGD.Lifetime` | fail with CS0234/CS0246/CS0012 naming `Lifetime` | references are not transitive |
| `control` | signal and lifetime, same code | build | the transitive canary fails for the missing reference, not for broken code |
| `autoref` | none, uses `UnityEngine.UI.Image` | build | the implicit uGUI reference is applied |

If the transitive canary ever builds, the harness has started passing references on, and every other
PASS is suspect.

### README snippets

Every fence tagged `csharp`, `cs` or `c#` whose body declares a `class`, `struct`, `interface`, `enum`
or `record` is compiled on its own, the way user code would see it:
- player variant;
- the package's auto-referenced runtime assemblies and those of its family dependencies, transitively, as
  UPM installs them;
- the engine and uGUI/TextMeshPro, as `Assembly-CSharp` sees them.

Fragments without a type declaration are skipped. Diagnostics point at the README line.

To exclude a fence that does declare a type, put this comment on the nearest non-blank line above the
opening fence. It does not render on GitHub or OpenUPM, and the text after `no-compile` is free:

```markdown
<!-- upm-tools: no-compile (pseudo-code: ellipses) -->
```

### The .meta check

The check covers each package root, which is also the root of its git checkout. Unity ignores dot-names,
names ending in `~` (`Samples~`, `Documentation~`), `cvs` and `*.tmp`, so these need no `.meta`.

| Label | Meaning |
| --- | --- |
| `MISSING` | a file or folder in the working tree has no `.meta` |
| `ORPHAN` | a `.meta` whose asset does not exist |
| `UNTRACKED-META` | a `.meta` that git does not track (`git ls-files`), so it would not be published |
| `MISSING-IN-GIT` | a tracked asset whose `.meta` is not tracked |
| `ORPHAN-IN-GIT` | a tracked `.meta` whose asset is not tracked, for example an empty folder: a clean clone, and therefore the published package, gets an orphan |
| `DUPLICATE-GUID` | two `.meta` files in the family share a GUID |

Git-ignored files are not considered.

### Output

The script prints one table per step, the deduplicated diagnostics (one line for a diagnostic that both
variants report), notes and a summary. It also writes:
- `level1-summary.txt` and `level1-report.json` (machine-readable) into the output folder. In the JSON,
  `deps` holds the asmdef-reference findings and `depsModules` holds the module check: findings, what each
  package uses and how it is covered, the modules every project has, and assemblies not checked;
- one build log per project into `logs/`.

Exit status: 0 if every step passed, 1 if the gate failed, 2 if the tools could not run.

### What level 1 cannot see

- How the code behaves on Mono or IL2CPP. Tests run on CoreCLR.
- PlayMode behaviour (Unity's player loop), and anything that calls into the engine.
- Package resolution and the immutable-folder behaviour of a real install.
- `.meta` files Unity would rewrite. Level 2 caught five such files in context.
- Unity's source generators, which Unity runs as analyzers and level 1 does not.
- Auto-referenced precompiled DLLs.
- IL2CPP stripping. That is the linker gate's job.
- A use of an engine module that leaves no reference in the compiled metadata (see
  [Built-in modules](#built-in-modules-the-deps-step)).

`harness/run.sh` is an older entry point, kept for scripts that still call it. It runs
`level1.sh --steps build,tests`.

## linker-gate.sh: IL2CPP stripping of context, commands and presenters

```sh
./linker-gate.sh                                   # 6000.0.41f1, Medium and High
./linker-gate.sh --editor 6000.0.41f1 --editor 6000.3.3f1
./linker-gate.sh --root /path/with/patched/checkouts   # try a fix on a copy
./linker-gate.sh --without-corelib                 # context only: --root needs just upm-lifetime and upm-context
```

For each editor the gate:
1. Compiles the family assemblies the probe uses from the checkouts, each against the references its asmdef
   declares, in this order: `com.openugd.lifetime`, `com.openugd.context`, then (unless `--without-corelib`)
   `com.openugd.signal`, `com.openugd.presenters`, `com.openugd.commands` and `com.openugd.corelib`, the last
   one against the engine modules as well. Then it compiles the probe, `linker/probe/App.cs` and
   `linker/probe/Corelib.cs`. It uses the editor's own Roslyn (`DotNetSdkRoslyn`), the editor's
   `netstandard.dll`, the player defines and `-optimize+`.
2. Runs the editor's own `UnityLinker` with the options the editor passes for an IL2CPP macOS player:
   `--use-editor-options --dotnetruntime=il2cpp --dotnetprofile=unityaot-macos`. It runs at rule set
   `Aggressive` (Medium) and `Experimental` (High), rooted by `linker/probe/root.xml`. With corelib, the
   engine modules are linked too, from the Mac standalone player's `Variations/mono/Managed` (the editor's own
   modules when that support is not installed).
3. Reads the stripped assemblies with `linker/inspect`, a System.Reflection.Metadata dumper built in the
   output folder, and checks:
   - the constructors the container calls survive: implicit, greedy without an attribute, and `[Inject]`.
     That covers services registered with `Add<T>()`, a command registered with
     `Map<TMessage>().RegisterCommand<T>()` (greedy constructor taking the message, the execution's `Lifetime`
     and a service), a command mapped with `Map<TMessage, TCommand>()` (`[Inject]` constructor), and
     presenters built by `ContextPresenterFactory.Create(typeof(T))` (greedy and `[Inject]` constructors);
   - every `[Inject]` field, property (with its setter) and constructor survives **and still carries
     `[Inject]`**, on services, commands and presenters. The container reads the attribute at runtime, so a
     removed attribute means injection silently does nothing;
   - `OpenUGD.InjectAttribute`, its constructor and its `Optional` setter survive;
   - no trim-analysis warning `IL2026`-`IL2123` in the linker output mentions `OpenUGD`: an annotation that
     does not reach the linker (`IL2087`, `IL2091`), a mismatch between an interface and its implementation
     (`IL2092`), a type handed to `Context.Instantiate` without its annotation (`IL2067`, `IL2077`).
4. Runs the stripped probe on the editor's Mono and requires every `CHECK` line to be `True`. The context
   checks cover a greedy constructor, Awake boot, an `[Inject]` constructor, field, property, private field and
   optional member, member injection after a factory, `Instantiate<T>()` of an unregistered type, and
   `Inject(target)`. The corelib checks tell a message to each of the three kinds of command registration
   (`RegisterCommand<T>`, `Map<TMessage, TCommand>`, a factory) through `context.Tell`, and attach presenters
   under a `Presenter.Root` with a `ContextPresenterFactory`: one built with `new` (injected before
   `OnInitialize`), two built by `Create(Type)`, and one built by the factory resolved as `IPresenterFactory`
   from the container. A scenario that throws prints its exception, so a stripped constructor shows up as the
   container's own message.

**Status on 2026-10-03 (last local run): the gate passes** on 6000.0.41f1 and 6000.3.3f1 at Medium and High
(47 checks per column). CI runs it on 6000.0.41f1 only. That it can fail was checked on a copy of the checkouts:
removing `[DynamicallyAccessedMembers]` from `RegisterCommand<TCommand>` strips `BuyCommand`'s constructor (the
registration then throws "has no public instance constructor") and produces `IL2087`; removing it from
`ContextPresenterFactory.Create` produces `IL2067` and `IL2092`. Before context gained its stripping support (2026-10-02: `[Inject]` derived from a linker
`Preserve` attribute, `[DynamicallyAccessedMembers]` on the registration entry points), the context half failed:
constructors and `[Inject]` property setters were stripped and the container refused to build.

A wrapper with an unannotated type parameter around `Add<T>()` in user code makes the gate fail. It
produces `IL2091 ... 'OpenUGD.ServiceCollectionExtensions.Add<TImpl>(...)'`, and the stripped container
rejects the type. That is why the probe never does it.

Limits:
- The gate does not build an IL2CPP player. The linker's output is what IL2CPP compiles, but the runtime
  check uses desktop Mono. Nothing the probe runs touches the engine, so the engine modules are linked but never
  loaded. `il2cpp-smoke.sh` builds and runs the real player.
- Mono cannot run every IL2CPP-mode linker output. With `--dotnetruntime=il2cpp`, UnityLinker removes interface
  methods nothing calls, and IL2CPP fills those vtable slots itself; Mono refuses to load such a type. Building a
  child context walks `Context.Contracts`, an iterator whose `IEnumerator.Reset` is removed, so on Mono it throws
  `TypeLoadException: VTable setup of type OpenUGD.Context+<get_Contracts>d__24 failed` at Medium and High. Linked
  with `--dotnetruntime=mono`, `Reset` stays and it passes; the IL2CPP player passes `child-context` too. The
  probe builds no child context, so the gate never meets this. A probe scenario added later that fails on Mono
  with that exception is not evidence about IL2CPP.
- Presenters are covered through `ContextPresenterFactory` and `Presenter.Root`/`AddPresenter`; views
  (`Presenter<TView>`, `ViewBehaviour`) and `ContextBehaviour` are not, because they need the engine at runtime.
- `IL5999` "unhandled reflection" notes on OpenUGD code are counted but do not fail the gate.

Exit status: 0 pass, 1 fail, 2 the tools could not run.

## level2.sh: the real-Unity gate

```sh
./level2.sh                                                  # all six packages, 6000.0.41f1
./level2.sh --packages upm-lifetime,upm-signal,upm-context
./level2.sh --editor 6000.0.41f1 --editor 6000.3.3f1
./level2.sh --delete-library                                 # free the disk afterwards
./level2.sh --tarball                                        # install what OpenUPM would publish (below)
./level2.sh --tarball --no-editor --smoke /tmp/smoke-dry     # pack and prepare only; Unity is not started
```

For each editor the script:
1. Creates or refreshes the smoke project (`--smoke`, else `config/family.json` → `smokeProject`) from
   `smoke-template/`:
   - `Packages/manifest.json` gets a `file:` reference to each selected checkout, its family dependencies
     included, and lists all of them under `testables`;
   - `com.unity.test-framework` and `com.unity.ugui` take the versions from the editor's package map;
   - `ProjectVersion.txt` is written for the editor;
   - each sample listed in a package's `package.json` is copied from `Samples~` to
     `Assets/Samples/<package display name>/<version>/<sample display name>`, as the Package Manager's
     Import button does;
   - the template's `ProjectSettings` holds only `EditorSettings.asset` (text serialization); Unity
     generates everything else with defaults.
2. Runs the editor in batchmode three times: an import (`-quit`), then `-runTests -testPlatform EditMode`
   and `-runTests -testPlatform PlayMode` with `-testResults`. If the import reports compile errors, the
   test runs are skipped.
3. Reports:
   - per test assembly, from the NUnit XML: total, passed, failed and skipped, plus the failures. A mode
     with no test assemblies in the selected packages is shown as `n/a`, not as a failure;
   - compile errors and warnings, `has no meta file` messages, orphan-`.meta` messages, and licence or
     package-resolution failures, from the editor logs (kept in `smoke/Logs/upm-tools/`);
   - per package checkout, from `git status`: every untracked `.meta`, with the ones this run created
     marked, and every tracked file the run changed;
   - Library size and free disk.

The script refuses to run if:
- the smoke project is in use: a process holds `Temp/UnityLockfile` (checked with `lsof`), or a
  `Unity -projectPath <smoke>` process is running;
- the smoke project sits inside a git work tree;
- less than 3 GB of disk is free.

It never builds a player. Switching editors deletes `Library` first, so one project serves both editors
without a second Library.

**`file:` packages are mutable.** Unity writes a missing `.meta` straight into the checkout, and it may
rewrite an existing hand-written one. That is why the script reports untracked and changed files per
checkout instead of `has no meta file`. That message only appears for immutable installs from a registry
or tarball: use `--tarball` for that. The script never commits or reverts anything in the checkouts. Review
what it reports and commit or discard it yourself.

### --tarball: the install users will get

OpenUPM clones a repository at its tag and publishes it with npm. `--tarball` reproduces that for each
selected package without a tag (`lib/pack.py`):

1. `git archive` of the checkout's HEAD into a scratch folder: tracked, committed files only, never `.git`,
   never anything untracked. Uncommitted changes are not in the tarball; the report lists the packages that
   had any.
2. `npm pack --ignore-scripts` in that folder, so npm's own rules apply as they would on OpenUPM: `.npmignore`,
   or `.gitignore` when there is no `.npmignore`, the `files` field, and npm's fixed exclusions. Without npm
   (or with `--no-npm`) the script writes the `.tgz` itself, every exported file under `package/`.
3. The tarballs go to `<smoke>/Tarballs/<name>-<version>.tgz`. The manifest points at them as
   `file:../Tarballs/<name>-<version>.tgz`, so Unity extracts them into `Library/PackageCache` and treats them
   as immutable, exactly like a registry package. They are listed under `testables` as before.
4. Samples are copied out of the tarballs, not the checkouts, into `Assets/Samples/<package display
   name>/<version>/<sample display name>`.

The report adds, before the per-editor sections:
- per package: the commit packed, file count, size, and whether the checkout had uncommitted changes;
- `NOT PACKED`: a file git tracks that the tarball lacks, because npm's rules dropped it. The family's
  `.gitignore` lists `[Bb]uild/`, `[Ll]ogs/` and `[Tt]emp/`, so a tracked folder of that name would be dropped
  from the published package. Dot-names are not reported: npm always leaves out `.gitignore` and `.npmignore`,
  and Unity never imports a dot-name. This fails the gate;
- `EXTRA`: a file in the tarball that the commit lacks (should never happen). This fails the gate;
- notes for `.gitattributes` rules that make `git archive` differ from a clone (`export-ignore`,
  `export-subst`, LFS).

The per-editor sections then report every `has no meta file, but it's in an immutable folder` message
(the asset is ignored; this fails the gate), compile errors, the samples imported and, as a note, sample
assets that ship without a `.meta` (Unity gives them new GUIDs on every import, which only matters when
something references them by GUID), and the EditMode and PlayMode results per test assembly. The package
checkouts are not inspected in this mode, because Unity cannot write into a tarball.

`--no-editor` stops after preparing the smoke project, with or without `--tarball`. Point `--smoke` at a
scratch folder to try the packing without touching the real smoke project. `level2-tarballs.json` next to
`level2-report.json` lists every packed file.

Exit status: 0 pass, 1 the gate failed, 2 the editor could not run (lock, licence, timeout) or a package could
not be packed. With `--no-editor` it is 0 when preparing (and packing) found nothing, 1 on a finding (`NOT PACKED`,
`EXTRA`, a sample that is missing), 2 when it could not run.

## il2cpp-smoke.sh: one IL2CPP player that boots a container

```sh
./il2cpp-smoke.sh                          # pack, build at Medium, serve, check in headless Chrome, stop the server
./il2cpp-smoke.sh --stripping high         # the same at High
./il2cpp-smoke.sh --serve                  # leave the server running and print its URL
./il2cpp-smoke.sh --no-build --serve       # serve and check the build already in the project
./il2cpp-smoke.sh --stop                   # stop a server left running by --serve
./il2cpp-smoke.sh --no-editor              # pack and prepare the project only; Unity is not started
```

The linker gate strips with the real UnityLinker but runs the result on desktop Mono. This script builds an actual
IL2CPP player, so AOT compilation, IL2CPP's generic sharing and the stripped runtime are exercised too. The player
is WebGL, which is always IL2CPP, so the editor's Mac IL2CPP module is not needed.

1. **Project.** It packs each family package from its checkout's HEAD the way OpenUPM publishes a tag (`lib/pack.py`,
   as `level2.sh --tarball` does) into `<project>/Tarballs`. It then creates or refreshes the throwaway project,
   `--project`, else `config/family.json` → `il2cppSmokeProject`, from `il2cpp-template/`:
   - `Packages/manifest.json` lists `com.unity.ugui` (version from the editor's package map) and a
     `file:../Tarballs/<name>-<version>.tgz` reference per family package. All six are installed by default, so
     widgets and ui are compiled into the player too;
   - `Assets/Il2CppSmoke/` holds the checks (`Boot.cs`, `Checks.cs`, `Services.cs` and three small MonoBehaviours)
     and the build script (`Editor/SmokeBuild.cs`). `Assets/Plugins/WebGL/OpenUGDSmoke.jslib` lets the player set
     `document.title`;
   - a file an earlier refresh copied and the template no longer has is removed with its `.meta`. Switching editors,
     or `--clean`, deletes `Library`.
2. **Build.** The editor runs in batchmode with `-buildTarget WebGL -executeMethod Il2CppSmoke.Editor.SmokeBuild.Run`.
   The build script recreates `Assets/Scenes/Boot.unity` (a camera and the `Boot` component) and sets:
   - IL2CPP, Managed Stripping Level `Medium` (`--stripping high`: `High`), a release build (not development),
     IL2CPP compiler configuration Release;
   - compression `Disabled`, so a plain static server can serve the build; no data caching; Code Optimization
     `BuildTimes` (only emscripten's optimisation level: stripping and IL2CPP's output are unaffected);
   - stack traces off for `Log` and `Warning`, so each check is one console line.

   Everything else stays at Unity's defaults, notably WebGL exception support "explicitly thrown exceptions only",
   as a shipped game has it. The script writes `Logs/upm-tools/build-result.json`: the BuildReport result, time, size,
   errors and the effective player settings. A failed build, compile errors or a missing result fail the run.
3. **Build folder and disk.** It requires `Build/WebGL/index.html` and one uncompressed `*.loader.js`,
   `*.framework.js`, `*.data` and `*.wasm` under `Build/WebGL/Build/`. It prints the build time, the build size
   per file, the largest folders in `Library`, then deletes `Library/Bee/artifacts/WebGL` (IL2CPP's C++ and the
   compiled objects), `Library/Il2cppBuildCache`, `Library/PlayerDataCache` and `Temp` unless `--keep-intermediates`
   is given, and prints Library size, project size and free disk.
4. **Server.** It starts `lib/static_server.py --port <port> --directory <build>` (Python's `http.server` handler on
   127.0.0.1, without the reverse DNS lookup that `python3 -m http.server` does before listening, which can stall for
   long on CI hosts) on a free port (or `--port`) in its own session, and checks, bypassing any proxy, that
   `index.html`, the loader, framework, `.data` and `.wasm` answer 200. It prints each content type; the `.wasm` is
   always sent as `application/wasm`, which Unity's loader needs for streaming compilation. The PID and URL go to
   `Logs/upm-tools/server.json`.
5. **Headless check.** It opens the page in headless Chrome, driven over the DevTools protocol through
   `--remote-debugging-pipe` with a throwaway profile (the user's own Chrome profile is never used), and waits up
   to `--check-timeout` seconds for `document.title` to carry the summary. It prints every check line from the
   console and any console error or page exception.
6. **Stop.** Without `--serve` it stops the server. With `--serve` it prints the URL and the commands that stop it:
   `./il2cpp-smoke.sh --stop`, or `kill <pid>`.

### What the player checks

`Boot` runs 29 checks through the packages' public API only. Each prints `OPENUGD-IL2CPP check <name>: PASS` or
`... FAIL <reason>`. Then it prints exactly one summary line, which the `.jslib` also writes into the page title:

- `OPENUGD-IL2CPP: PASS <n>/<n>`;
- `OPENUGD-IL2CPP: FAIL <k>/<n> <names>`, where `k` is the number of failed checks and `names` lists exactly those
  checks, comma-separated.

The set of checks is fixed. A check that cannot run (its context did not build, or the 60-second in-player timeout
passed) counts as failed.

| Area | Checks |
| --- | --- |
| player | `player-il2cpp`: a player, built with `ENABLE_IL2CPP` |
| lifetime | `lifetime-nesting`: one LIFO sequence of actions and nested scopes, cascade, `AsCancellationToken`; `lifetime-terminated`: registering on a terminated lifetime runs at once, a nested scope is born terminated |
| signal | `signal-struct-state`: a signal of the game's own on `SignalBase.Dispatch<TState>` with a struct state, unsubscribed by its lifetime; `signal-generic-state`: the `Signal3<T1,T2,T3>` from the docs over value types (a `ValueTuple` state); `signal-covariance`: `Signal<string>` used as `ISignal<object>` |
| logging | `log-unity-sink`: `LogRoot.UseUnityConsole` reaches `Debug.Log` with its tag, and stops when its lifetime ends |
| context | `context-build`: `BuildAsync` on `PlaySession.Lifetime`, with an `IAwakeService` that awaits `Task.Yield()`; `constructor-implicit`, `constructor-greedy` (widest of three), `constructor-inject` (`[Inject]` beats a wider constructor); `inject-field`, `inject-property`, `inject-private-field`, `inject-optional` (`[Inject(Optional = true)]` property and field, the field keeping its initializer); `boot-awake-initialize` (both phases, in order, resumed on the main thread); `collection-elements` (`AsElementOf` + `IReadOnlyList<T>` in registration order); `collection-empty` (a list nothing contributes to: an array type no code creates); `child-context` (inherit, shadow, dispose its own only); `instantiate-unregistered` (`Instantiate<T>()` and `Instantiate<T>(args)`); `inject-monobehaviour` (`Context.Inject` on a component added at run time); `context-dispose` |
| presenters | `presenter-attach-inject` (`new` + `AddPresenter` under a `Presenter.Root` over `ContextPresenterFactory`: `[Inject]` members before `OnInitialize`); `presenter-factory-create` (`Create(typeof(T))`: greedy constructor plus `[Inject]` member, and the factory resolved as `IPresenterFactory`); `presenter-view` (`Presenter<HudView>` with a `ViewBehaviour` view: `ViewLifetime`, `SetView(null)`); `presenter-close` |
| commands | `command-register-tell` (`Map<Buy>().RegisterCommand<BuyCommand>()` and `Tell`: greedy constructor with the message, the execution `Lifetime` and a service, plus an `[Inject]` member); `command-unregister` (ending the registration) |
| corelib host | `context-behaviour`: a `ContextBehaviour` added at run time boots its own context from `Awake`, `OnStarted` runs, `OnUpdate` fires |

`Checks.cs` and `Services.cs` reference no engine type. `tests/test_il2cpp_smoke.py` compiles them with
`tests/fixtures/il2cpp-smoke/Harness.cs` and runs the 24 engine-free checks on the editor's Mono, as compiled and
after UnityLinker at Medium. That proves each check's expectation before a player is built. After the linker it
tolerates only the Mono vtable failure described under the linker gate's limits.

### Safety and disk

The script refuses to run when:
- the project sits inside a git work tree;
- a process holds its `Temp/UnityLockfile`, or a Unity process runs on it;
- it is about to build and less than `--min-free-gb` (4 GB) of disk is free.

While the editor runs, it checks free disk every 5 s and the project size every 30 s. It stops the editor's whole
process group when free disk drops under `--min-free-gb` or the project grows past `--max-project-gb` (6 GB), and
says which. A server left running by an earlier `--serve` is stopped first. Packing only reads the checkouts
through git.

Measured on 2026-10-03 (6000.0.41f1, Medium, all six packages, first build from an empty Library): editor run
175 s, BuildReport 146 s, about 3 min 10 s end to end. Build 15.3 MB: `.wasm` 11.3 MB, `.data` 3.7 MB, framework
344 KB, loader 19 KB. The project peaked at 268 MB during the build. Deleting `Library/Bee/artifacts/WebGL` freed
194 MB, leaving a 68 MB Library and an 84 MB project. Free disk stayed above 11 GB. Sizes here are decimal; the
script prints binary units (`MB` = MiB).

At High, right after (Library warm, IL2CPP output deleted): editor run 115 s, BuildReport 98 s. Build 14.0 MB:
`.wasm` 10.3 MB, `.data` 3.3 MB. Peak 239 MB, 162 MB freed afterwards.

**Status on 2026-10-03 (last local run): the gate passes.** The IL2CPP WebGL players built at Medium and at High
each reported `OPENUGD-IL2CPP: PASS 29/29` in headless Chrome, `child-context` included.

### Output and exit status

The report goes to `<project>/Logs/upm-tools/il2cpp-smoke-report.json`, the editor log to
`<project>/Logs/upm-tools/<editor>-<stamp>-build-<level>.log`, the server's log to `.../server.log`. The last line is
`IL2CPP SMOKE: PASS (<summary>)`, `FAIL`, `NOT RUN TO THE END`, or, with `--no-check`,
`BUILT AND SERVED, PLAYER NOT CHECKED`.

Exit status:
- 0: the player reported PASS, or with `--no-check`, the build and the HTTP checks passed;
- 1: the gate failed: a compile or build error, an incomplete build folder, an HTTP error, a tracked file missing
  from a tarball, or the player reported FAIL, a malformed summary or nothing within `--check-timeout`;
- 2: the tools could not run: the project is inside git or in use, too little disk, the build was stopped, an
  invalid licence, an editor timeout, a package that cannot be packed, or no browser without `--no-check`.

A licence failure is recognised from the editor log ("No valid Unity Editor license", "License is not active" and
similar). The script then says to sign in to Unity Hub and run again.

Limits:
- One target (WebGL) and one player configuration. Mac, iOS and Android IL2CPP players are not built. Code
  generation is IL2CPP's default ("Faster runtime").
- Default exception support: a `NullReferenceException` raised by the runtime is not catchable in this player. The
  checks therefore test for null rather than dereference, and a crash shows as no summary (`NO RESULT`).
- Headless Chrome renders WebGL through SwiftShader. The checks never depend on rendering.

## release-check.sh: before tagging

```sh
./release-check.sh                                  # all six packages, as config/family.json "release.version"
./release-check.sh --packages upm-corelib --version 2.0.1   # one package, a later patch
./release-check.sh --json /tmp/release.json
```

It reads the committed tree of each checkout's HEAD, which is what a tag would publish, and tags nothing.

| Check | Fails when |
| --- | --- |
| `version` | `package.json` `version` is not the planned version (`--version`, default `release.version` in `config/family.json`: `2.0.0`). The tag is the version itself, as the existing tags are (lifetime's `1.2.0`, corelib's `0.6.1`), so this is the "tag does not equal package.json version" refusal. This has happened once: corelib's tag `1.2.0` (since deleted) was published on OpenUPM as `0.2.0` (the commit titled `1.2.0`, `1df0781`, sets `version` to `0.2.0`). |
| `tag` | a tag of that name already exists and does not point at HEAD. A published version cannot change. |
| `changelog` | the first `## ` heading of `CHANGELOG.md` is neither `## [Unreleased]` nor `## [<version>]`, or the file has both (one release, two sections). An `[Unreleased]` heading passes with a note: rename it `## [<version>] - <date>` in a release commit, then run the check again, because the printed tag command names the HEAD that was checked. |
| `readme` | the `README.md` section whose heading starts with "Install" does not pin the version in all three forms: `openupm add <name>@<version>`, a scoped-registry entry `"<name>": "<version>"`, and a git URL `"<name>": "<repository>.git#<version>"` matching `package.json` `repository`. The git form must also list every family package the package needs, transitively, each pinned to a tag of the same major at or above the declared minimum, because git URLs do not resolve OpenUPM dependencies. |
| `family deps` | a `com.openugd.*` dependency is not declared at a minimum of the planned major (`2.x`), or its minimum is above that package's own version under `--root` (the install would not resolve). |
| `unity` | `package.json` `unity` is not `release.unity` (`6000.0`). |
| `samples` | a `samples[].path` does not exist in HEAD. |
| `meta` | a file or folder in HEAD that Unity imports (not a dot-name, not inside a `~` folder) has no tracked `.meta` (`MISSING-IN-HEAD`), or a tracked `.meta` lacks its asset (`ORPHAN-IN-HEAD`). |
| `clean` | the worktree has staged, unstaged or untracked changes. |

It then prints the commands that would tag each HEAD, grouped in publication layers computed from the family
dependencies (lifetime; signal and context; corelib; widgets; ui as an independent leaf), with the
`https://package.openupm.com/<name>` registry documents to wait for between layers. A package whose version
does not match, or whose tag already exists on another commit, is printed as `REFUSED`, without a command. A
package with any other finding, or with a selected family dependency that is not ready (transitively: a finding
of its own or a dependency that is not ready), is printed as `# BLOCKED (...)`, commented out. A repository
without an `origin` remote gets a reminder instead of a push command.

The tag goes on the commit that was checked. After the merge into the default branch (OpenUPM shows the README
of the default branch) it is the same commit when the merge is a fast-forward. The check does not
run the gates: run `level1.sh`, `linker-gate.sh` and `level2.sh --tarball` on the same commits first.

Exit status: 0 every package is ready, 1 at least one is not, 2 the tools could not run.

## finish.sh: landing the work (maintainer only)

```sh
./finish.sh            # dry run: what `git merge --ff-only` would do in each main checkout
./finish.sh --apply    # do it where it is safe
```

This script is for the maintainer's own machine and is not needed to check the packages. It assumes that each
folder under `--root` is a linked checkout of a local repository (its main checkout), on the branch `sourceBranch`
(`feature/v2-exec` for 2.0), and that `target` is a branch of that repository. The source branch, and the
`feature/v2` targets, exist only in the maintainer's local repositories, not on GitHub, so with ordinary clones
each package repo is reported as blocked ("branch ... does not exist") and the script exits with 1.

The script reads the repos from `config/family.json`:
- `packages[].target`: `feature/v2`, or `main` for `upm-context`;
- `finishExtra`: the maintainer's local Unity project that holds the checkouts (`main`); its `path` is used when
  `<root>/host` does not exist (relative to the upm-tools folder, like every path in the file);
- `held`: branches the script never merges, only reports, each with the reason. Empty now: for 2.0 it held
  `upm-dependency-injection`'s `feature/deprecation-banner`, merged by hand once `com.openugd.context` 2.0.0 was
  live on OpenUPM;
- `outsideTrain`: repos checked by level 1 on request but not part of the 2.0 release (`upm-configuration`).

It finds each main checkout through the worktree's `git rev-parse --git-common-dir` and prints, per repo:
- the commits a fast-forward would bring and the diffstat;
- whether it is a fast-forward;
- whether the checkout is clean and on the target branch.

`--apply` merges only where all three hold:
- no staged or unstaged changes to tracked files;
- the target branch is checked out;
- the target is an ancestor of the source.

git itself still refuses if an untracked file would be overwritten. The script never switches branches,
never moves another ref and never pushes. It prints the `git push` commands to run later, and says when
a repo has no remote yet.

Exit status: 0 nothing blocked, 1 at least one repo cannot be fast-forwarded as things stand, 2 the tools
could not run.

## Configuration

| File | Holds |
| --- | --- |
| `config/family.json` | checkout root, smoke project path, the IL2CPP smoke project path (`il2cppSmokeProject`), the package repos in dependency order, the branches `finish.sh` uses (maintainer only), the planned release version and Unity minimum (`release`) |
| `config/unity/<version>.json` | package-version map, global defines (common/editor/player), `noWarn`, which Unity packages are compiled from source |
| `config/test-floors.json` | minimum passed tests per test asmdef |

## Testing the tools

```sh
python3 -m unittest discover -s tests
```

The tests cover the version and range evaluation, `defineConstraints`, the Unity define ladder, the module
check (its rules on a synthetic editor, the map read from each installed editor, and level 1 on the
AudioListener fixture, which needs the default editor and the .NET SDK and is skipped without them), README
fence extraction with the opt-out marker, the `.meta` check on a throwaway git repository, the tarball packing
(only committed files, npm's ignore rules reported, samples copied out of the tarball; the npm case is skipped
without npm), and `release-check.sh` on a throwaway family of two or three packages (version refusal, an existing
tag, README pins and dependency lists, CHANGELOG headings, dependency minimums, `.meta` read from HEAD, a dirty
worktree, layered tag commands, blocking through a dependency's dependency), and `il2cpp-smoke.sh`. Its tests cover the
summary and check-line parsing, `Boot.Checks` against the checks the script runs, the project refresh and manifest,
the build-folder check, the disk guard (a real process group stopped), licence detection, the server (start, 200s,
`application/wasm`, `--stop`), and the headless check against a local page (skipped without Chrome). They run `main()`
end to end against a fake editor (`tests/fixtures/il2cpp-smoke/fake_unity.py`) for a licence failure, a compile
error, a passing and a failing page. Finally they compile the template's C# against the checkouts with the editor's
Roslyn and run the engine-free checks on Mono before and after UnityLinker (skipped without the default editor).
The CI helpers are covered too: the xar table-of-contents reader on synthetic archives, the cpio subset by running
the real `curl -r | gzip -dc | cpio -i` pipeline on a fake installer, and the clone commands.

## CI

`.github/workflows/ci.yml` runs on every push to `main`, every pull request, every Monday (the package repos move
on their own) and on demand:

- **lint** (Ubuntu 24.04, pinned because Python 3.9 has no build for 26.04): `lib/`, `tests/` and `ci/` compile on
  Python 3.9, the oldest supported; every `*.sh` parses; every `config` JSON parses.
- **gates** (macOS 15, arm64): the tools' own tests, `level1.sh` on the train (the six packages released
  together as 2.0.0), `level1.sh --packages upm-configuration`, and `linker-gate.sh`, each run even when an
  earlier one failed. The reports and logs
  (`level1-report.json` and the summary of each level 1 run, its restore log and build logs,
  `linker-gate-report.json` and the UnityLinker logs) are uploaded as the `gate-reports` artifact.

Neither job installs Unity or needs a licence. `ci/fetch-editor.py` reads the editor installer's xar table of contents
with an HTTP range request and streams only its payload (a 5 GB gzip cpio stream) through `cpio`, keeping the files
the gates read: the reference DLLs, netstandard, Roslyn, the .NET runtime, Mono, UnityLinker, the built-in packages,
the template script assemblies, the NUnit and Test Framework tarballs and the module lists, about 2.3 GB of the
8.7 GB editor. That folder is cached per editor version and per version of the script, and saved right after the
fetch, so a failing gate does not cost the next run the 5 GB download. A stalled download is retried from the start.
`ci/clone-packages.py` shallow-clones the repos in `config/family.json` (`packages` and `outsideTrain`) from
`github.com/openugd` at their default branches. A manual run can name one branch or tag for the train
(`packages-ref`, for example `2.0.0`); the repos outside the train stay on their default branch.

`level2.sh` and `il2cpp-smoke.sh` start the editor, so they need a full install with an activated licence and are
not in CI: run them on a machine where Unity is installed. CI therefore runs no `RequiresUnity` test.
Of the PlayMode test assembly `com.openugd.corelib.playmode.tests` it runs only the engine-free tests, on CoreCLR
and outside Unity's player loop.
The badge at the top shows the result of the CI gates. The dated statuses in this README ("Status on
2026-10-03") are the last local runs of what CI does not cover: the IL2CPP players and the linker gate on
6000.3.3f1. Level 2, with the `RequiresUnity` and PlayMode tests, was run locally in Unity 6000.0.41f1 before
the 2.0.0 release.

GitHub disables scheduled workflows in a public repository after 60 days without repository activity, so the
Monday run needs an occasional commit to this repository. If it has been disabled, enable it again in the
Actions tab (or with `gh workflow enable ci.yml`). A manual run (Run workflow) checks the package repos at any
time.

To reproduce the CI job locally:

```sh
python3 ci/fetch-editor.py --version 6000.0.41f1 --changeset 46e447368a18 --dest /tmp/unity-editors
python3 ci/clone-packages.py --dest /tmp/packages
OPENUGD_UNITY_EDITORS=/tmp/unity-editors OPENUGD_ROOT=/tmp/packages ./level1.sh
```

## Licence

Apache-2.0. See [LICENSE.md](LICENSE.md).
