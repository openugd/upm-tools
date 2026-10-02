# upm-tools

Regression tooling for the OpenUGD package family (`com.openugd.*`). It implements the two-level gate
from the v2 decisions ("CI у два рівні") and audit item P0-6. The tooling lives here, not in any package
repo, because it checks all the packages side by side.

| Script | When to run it | Needs | Typical time |
| --- | --- | --- | --- |
| `level1.sh` | every commit, every package | .NET SDK, an installed editor (its files only) | 25-30 s |
| `linker-gate.sh` | changes to `com.openugd.context` registration or `[Inject]`; before a release | an installed editor (its files only) | 15 s per editor |
| `level2.sh` | before a release, or after `.meta`/asmdef/package.json changes | Unity with an activated licence | about 1 min for three packages (measured from an empty Library) |
| `finish.sh` | when the v2 work is ready to land in the main checkouts | git | instant |

Every script prints its options with `--help`. None of them pushes, adds a remote or rewrites history.

## Requirements

- macOS with Unity editors installed through Unity Hub under `/Applications/Unity/Hub/Editor/`.
  `6000.0.41f1` is the default. `6000.3.3f1` is also supported. Both install layouts are handled: 6000.0
  keeps the scripting files in `Unity.app/Contents`, 6000.3 in `Unity.app/Contents/Resources/Scripting`.
- Python 3.9 or newer (the system `python3` is enough; no third-party modules).
- .NET SDK 10 (`dotnet`) for level 1, its metadata reader (`asmrefs/`) and the linker gate's inspector. The
  test projects restore NUnit
  3.14.0, NUnit3TestAdapter 4.6.0 and Microsoft.NET.Test.Sdk 17.11.1 from NuGet (or the local NuGet cache).
- `level2.sh` only: a Unity licence already activated in Unity Hub (Personal is fine), because it starts
  the editor in batchmode.

The package checkouts are found under `config/family.json` → `root`
(`~/workspace/openugd/v2/wt`), one folder per repo (`upm-lifetime`, `upm-signal`,
`upm-context`, `upm-corelib`, `upm-corelib-widgets`, `upm-ui`). Override it with `--root` or
`OPENUGD_ROOT`. Build output goes to `--out`, else `$OPENUGD_HARNESS_OUT`, else `./out` (ignored by
git). Give every concurrent run its own output folder.

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
- The default map, `6000.0.41f1.json`, lists the Unity packages the OpenUGD host project resolves on
  6000.0.41f1 (its `Packages/manifest.json` and `packages-lock.json`): `com.unity.ugui` 2.0.0,
  `com.unity.test-framework` 1.4.6, `com.unity.ext.nunit` 2.0.5 and its `com.unity.modules.*` set.
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
- PlayMode, and anything that calls into the engine.
- Package resolution and the immutable-folder behaviour of a real install.
- `.meta` files Unity would rewrite. Level 2 caught five such files in context.
- Unity's source generators, which Unity runs as analyzers and level 1 does not.
- Auto-referenced precompiled DLLs.
- IL2CPP stripping. That is the linker gate's job.
- A use of an engine module that leaves no reference in the compiled metadata (see
  [Built-in modules](#built-in-modules-the-deps-step)).

`harness/run.sh` is kept for callers of the seed harness. It runs `level1.sh --steps build,tests`.

## linker-gate.sh: IL2CPP stripping of com.openugd.context (P0-4)

```sh
./linker-gate.sh                                   # 6000.0.41f1, Medium and High
./linker-gate.sh --editor 6000.0.41f1 --editor 6000.3.3f1
./linker-gate.sh --root /path/with/patched/upm-context   # try a fix on a copy
```

For each editor the gate:
1. Compiles `com.openugd.lifetime` and `com.openugd.context` from the checkouts, then the probe
   `linker/probe/App.cs`. It uses the editor's own Roslyn (`DotNetSdkRoslyn`), the editor's
   `netstandard.dll`, the player defines and `-optimize+`.
2. Runs the editor's own `UnityLinker` with the options the editor passes for an IL2CPP macOS player:
   `--use-editor-options --dotnetruntime=il2cpp --dotnetprofile=unityaot-macos`. It runs at rule set
   `Aggressive` (Medium) and `Experimental` (High), rooted by `linker/probe/root.xml`.
3. Reads the stripped assemblies with `linker/inspect`, a System.Reflection.Metadata dumper built in the
   output folder, and checks:
   - the constructors the container calls survive: implicit, greedy without an attribute, and `[Inject]`;
   - every `[Inject]` field, property (with its setter) and constructor survives **and still carries
     `[Inject]`**. The container reads the attribute at runtime, so a removed attribute means injection
     silently does nothing;
   - `OpenUGD.InjectAttribute`, its constructor and its `Optional` setter survive;
   - no `IL2091`/`IL2087` line in the linker output mentions `OpenUGD`.
4. Runs the stripped probe on the editor's Mono and requires every `CHECK` line to be `True`. The checks
   cover a greedy constructor, Awake boot, an `[Inject]` constructor, field, property, private field and
   optional member, member injection after a factory, `Instantiate<T>()` of an unregistered type, and
   `Inject(target)`.

**Status on 2026-10-02: the gate fails, as expected, because the P0-4 fix is not in `upm-context` yet.**
Constructors and `[Inject]` property setters are stripped, and the stripped container refuses to build
("has no public instance constructor, and none is marked [Inject]").

**The gate passes on both editors and both levels once the fix is applied.** This was verified on a copy
of today's `upm-context` with these changes:
- `InjectAttribute : PreserveAttribute`, with `PreserveAttribute` no longer sealed;
- an internal polyfill of `System.Diagnostics.CodeAnalysis.DynamicallyAccessedMembersAttribute`;
- `[DynamicallyAccessedMembers(PublicConstructors | NonPublicConstructors)]` on the type parameters or
  `Type` parameters of both `ServiceCollectionExtensions.Add<TImpl>` overloads, `TryAdd<,TImpl>`,
  `RegistrationExtensions.Add<TImpl>`, both `ContextExtensions.Instantiate<T>`,
  `ServiceCollection.Add(Type, ...)` and `Context.Instantiate(Type, ...)`.

A wrapper with an unannotated type parameter around `Add<T>()` in user code makes the gate fail. It
produces `IL2091 ... 'OpenUGD.ServiceCollectionExtensions.Add<TImpl>(...)'`, and the stripped container
rejects the type. That is why the probe never does it.

Limits:
- The gate does not build an IL2CPP player. The linker's output is what IL2CPP compiles, but the runtime
  check uses desktop Mono.
- The probe covers context's own entry points only. Registration helpers in corelib (`AddWindow<T>`,
  `RegisterCommand<T>`) are not linked yet.
- `IL5999` "unhandled reflection" notes on OpenUGD code are counted but do not fail the gate.

Exit status: 0 pass, 1 fail, 2 the tools could not run.

## level2.sh: the real-Unity gate

```sh
./level2.sh                                                  # all six packages, 6000.0.41f1
./level2.sh --packages upm-lifetime,upm-signal,upm-context
./level2.sh --editor 6000.0.41f1 --editor 6000.3.3f1
./level2.sh --delete-library                                 # free the disk afterwards
```

For each editor the script:
1. Creates or refreshes the smoke project, by default `~/workspace/openugd/v2/smoke`,
   from `smoke-template/`:
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
or tarball, which this script does not do yet. The script never commits or reverts anything in the
checkouts. Review what it reports and commit or discard it yourself.

Exit status: 0 pass, 1 the gate failed, 2 the editor could not run (lock, licence, timeout).

## finish.sh: landing the work

```sh
./finish.sh            # dry run: what `git merge --ff-only` would do in each main checkout
./finish.sh --apply    # do it where it is safe
```

The script reads the repos from `config/family.json`:
- `packages[].target`: `feature/v2`, or `main` for `upm-context`;
- `finishExtra`: the host project (`main`) and `upm-dependency-injection`, which lands
  `feature/deprecation-banner` on `master`.

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
a repo has no remote yet (`upm-context` and the host project today).

Exit status: 0 nothing blocked, 1 at least one repo cannot be fast-forwarded as things stand, 2 the tools
could not run.

## Configuration

| File | Holds |
| --- | --- |
| `config/family.json` | checkout root, smoke project path, the package repos in dependency order, finish targets |
| `config/unity/<version>.json` | package-version map, global defines (common/editor/player), `noWarn`, which Unity packages are compiled from source |
| `config/test-floors.json` | minimum passed tests per test asmdef |

## Testing the tools

```sh
python3 -m unittest discover -s tests
```

The tests cover the version and range evaluation, `defineConstraints`, the Unity define ladder, the module
check (its rules on a synthetic editor, the map read from each installed editor, and level 1 on the
AudioListener fixture, which needs the default editor and the .NET SDK and is skipped without them), README
fence extraction with the opt-out marker, and the `.meta` check on a throwaway git repository.
