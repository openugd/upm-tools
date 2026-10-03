// The IL2CPP smoke player: the only component in the Boot scene. It exercises, through the packages' public API
// only, what managed code stripping and AOT compilation can break, and reports one console line per check:
//
//   OPENUGD-IL2CPP check <name>: PASS
//   OPENUGD-IL2CPP check <name>: FAIL <reason>
//
// then exactly one summary line, also written into the page title through Plugins/WebGL/OpenUGDSmoke.jslib so a
// headless browser can read document.title:
//
//   OPENUGD-IL2CPP: PASS <n>/<n>
//   OPENUGD-IL2CPP: FAIL <k>/<n> <comma-separated names of the k failed checks>
//
// The set of checks is fixed (Checks below): a check that could not run, because the context it needs did not
// build or the run timed out, still counts, as a failure. The engine-free checks live in Checks.cs; this file adds
// the ones that need the engine (a MonoBehaviour injected, a presenter with a ViewBehaviour view, UnityLogSink,
// corelib's ContextBehaviour). il2cpp-smoke.sh in upm-tools builds, serves and reads this.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using OpenUGD;
using OpenUGD.Core;
using OpenUGD.Logging;
using OpenUGD.Presenters;
using UnityEngine;

namespace Il2CppSmoke
{
    public sealed class Boot : MonoBehaviour
    {
        public const string Prefix = "OPENUGD-IL2CPP";

        /// Every check, in the order it runs.
        public static readonly string[] Checks =
        {
            "player-il2cpp",
            "lifetime-nesting",
            "lifetime-terminated",
            "signal-struct-state",
            "signal-generic-state",
            "signal-covariance",
            "log-unity-sink",
            "context-build",
            "constructor-implicit",
            "constructor-greedy",
            "constructor-inject",
            "inject-field",
            "inject-property",
            "inject-private-field",
            "inject-optional",
            "boot-awake-initialize",
            "collection-elements",
            "collection-empty",
            "child-context",
            "instantiate-unregistered",
            "inject-monobehaviour",
            "presenter-attach-inject",
            "presenter-factory-create",
            "presenter-view",
            "presenter-close",
            "command-register-tell",
            "command-unregister",
            "context-dispose",
            "context-behaviour",
        };

        private const float TimeoutSeconds = 60f;

        private readonly Dictionary<string, string> _failures = new Dictionary<string, string>();
        private readonly HashSet<string> _done = new HashSet<string>();
        private float _deadline;
        private string _summary;

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void OpenUGDSmokeSetTitle(string title);
#endif

        private IEnumerator Start()
        {
            _deadline = Time.realtimeSinceStartup + TimeoutSeconds;
            Debug.Log(Prefix + " boot: Unity " + Application.unityVersion + ", " + Application.platform + ", " +
                      Backend + (Debug.isDebugBuild ? ", development build" : ", release build"));

            // --- engine-free packages ----------------------------------------------------------------------------
            Check("player-il2cpp", PlayerIsIl2Cpp);
            Check("lifetime-nesting", EngineFreeChecks.LifetimeNesting);
            Check("lifetime-terminated", EngineFreeChecks.LifetimeTerminated);
            Check("signal-struct-state", EngineFreeChecks.SignalStructState);
            Check("signal-generic-state", EngineFreeChecks.SignalGenericState);
            Check("signal-covariance", EngineFreeChecks.SignalCovariance);
            Check("log-unity-sink", LogUnitySink);

            // --- the root context: BuildAsync, with an Awake step that resumes on a later frame -------------------
            Task<Context> build = null;
            string failure = null;
            try
            {
                build = EngineFreeChecks.BuildRoot(PlaySession.Lifetime);
            }
            catch (Exception e)
            {
                failure = "threw " + Describe(e);
            }

            while (build != null && !build.IsCompleted) yield return null;

            Context root = null;
            if (failure == null)
            {
                if (build.IsFaulted) failure = "BuildAsync threw " + Describe(Unwrap(build.Exception));
                else if (build.IsCanceled) failure = "BuildAsync was cancelled";
                else root = build.Result;
            }

            if (root == null)
            {
                Record("context-build", failure ?? "BuildAsync returned null");
                Skip("the root context did not build");
            }
            else
            {
                Record("context-build", PlaySession.Lifetime.IsTerminated ? "PlaySession.Lifetime is terminated" : null);
                var c = new ContextChecks(root);
                Check("constructor-implicit", c.ConstructorImplicit);
                Check("constructor-greedy", c.ConstructorGreedy);
                Check("constructor-inject", c.ConstructorInject);
                Check("inject-field", c.InjectField);
                Check("inject-property", c.InjectProperty);
                Check("inject-private-field", c.InjectPrivateField);
                Check("inject-optional", c.InjectOptional);
                Check("boot-awake-initialize", c.BootAwakeInitialize);
                Check("collection-elements", c.CollectionElements);
                Check("collection-empty", c.CollectionEmpty);
                Check("child-context", c.ChildContext);
                Check("instantiate-unregistered", c.InstantiateUnregistered);
                Check("inject-monobehaviour", () => InjectMonoBehaviour(root));
                Check("presenter-attach-inject", c.PresenterAttachInject);
                Check("presenter-factory-create", c.PresenterFactoryCreate);
                Check("presenter-view", () => PresenterView(c.Presenters));
                Check("presenter-close", c.PresenterClose);
                Check("command-register-tell", c.CommandRegisterTell);
                Check("command-unregister", c.CommandUnregister);
                Check("context-dispose", c.ContextDispose);
            }

            // --- ContextBehaviour: corelib's MonoBehaviour host, booted from Awake ------------------------------
            SmokeContextBehaviour host = null;
            GameObject hostObject = null;
            string hostFailure = null;
            try
            {
                hostObject = new GameObject("SmokeContextBehaviour");
                host = hostObject.AddComponent<SmokeContextBehaviour>();   // Awake runs here and starts the boot
                if (host.Startup == null) hostFailure = "Startup is null after Awake";
            }
            catch (Exception e)
            {
                hostFailure = "threw " + Describe(e);
            }

            if (hostFailure != null)
            {
                Record("context-behaviour", hostFailure);
            }
            else
            {
                while (!host.Startup.IsCompleted) yield return null;
                // OnStarted subscribes to OnUpdate; give the loop a few frames to fire it.
                for (var i = 0; i < 5 && host.Updates == 0; i++) yield return null;
                Context hosted = null;
                Check("context-behaviour", () =>
                {
                    if (host.Startup.IsFaulted) return "Startup faulted: " + Describe(Unwrap(host.Startup.Exception));
                    hosted = host.Context;
                    if (hosted == null) return "Context is null after Startup";
                    if (!host.Started) return "OnStarted was not called";
                    if (!(hosted.Resolve<IClock>() is Clock)) return "IClock did not resolve to Clock";
                    var booted = hosted.Resolve<Booted>();
                    if (!booted.AwokeAfterYield || !booted.Initialized) return "its services did not boot";
                    return host.Updates > 0 ? null : "OnUpdate did not fire within 5 frames";
                });
                Destroy(hostObject);
                yield return null;
                if (hosted != null && !hosted.Lifetime.IsTerminated)
                    Debug.Log(Prefix + " note: the ContextBehaviour's context outlived its destroyed GameObject");
            }

            Finish(null);
        }

        private void Update()
        {
            if (_summary == null && Time.realtimeSinceStartup > _deadline)
            {
                StopAllCoroutines();
                Finish("timed out after " + TimeoutSeconds + " s");
            }
        }

        private void OnGUI()
        {
            if (_summary == null) return;
            GUI.Label(new Rect(16, 16, Screen.width - 32, Screen.height - 32), _summary);
        }

        // --- checks that need the engine -------------------------------------------------------------------------

        private static string PlayerIsIl2Cpp()
        {
            if (Application.isEditor) return "running in the editor, not in a player";
            return Backend == "IL2CPP" ? null : "scripting backend is " + Backend;
        }

        private static string Backend
        {
            get
            {
#if ENABLE_IL2CPP
                return "IL2CPP";
#else
                return "Mono";
#endif
            }
        }

        private static string LogUnitySink()
        {
            const string probe = "il2cpp-log-probe";
            var scope = Lifetime.Eternal.DefineNested("log");
            var log = new LogRoot("Smoke");
            var seen = new List<string>();
            Application.LogCallback hook = (condition, trace, type) => {
                if (condition != null && condition.Contains(probe)) seen.Add(condition);
            };
            Application.logMessageReceived += hook;
            try
            {
                log.UseUnityConsole(scope.Lifetime);
                log.WithTag("Boot").Info(probe);
                scope.Terminate();
                log.Info(probe + " after the sink's lifetime ended");
            }
            finally
            {
                Application.logMessageReceived -= hook;
                log.Dispose();
            }

            if (seen.Count == 0) return "nothing reached the Unity console";
            if (seen.Count > 1) return "the sink still wrote after its lifetime ended";
            return seen[0].Contains("Boot") ? null : "the console line lost its tag: " + seen[0];
        }

        private static string InjectMonoBehaviour(Context root)
        {
            var go = new GameObject("InjectTarget");
            try
            {
                var target = go.AddComponent<InjectTarget>();
                root.Inject(target);
                if (target.Clock == null) return "[Inject] property on the MonoBehaviour left null";
                if (target.Save == null) return "[Inject] private field on the MonoBehaviour left null";
                return target.Localization == null ? null : "an optional member with no registration was set";
            }
            finally
            {
                Destroy(go);
            }
        }

        private static string PresenterView(Presenter.Root presenters)
        {
            if (presenters == null) return "no Presenter.Root (presenter-attach-inject failed)";
            var go = new GameObject("HudView");
            try
            {
                var view = go.AddComponent<HudView>();
                var p = presenters.AddPresenter(new HudViewPresenter());
                if (p.Clock == null) return "[Inject] property on Presenter<HudView> left null";
                p.SetView(view);
                if (p.ViewAdded != 1 || p.Refreshed < 1) return "SetView(view) did not call OnViewAdded and OnRefresh";
                var viewLifetime = p.CapturedViewLifetime;
                if (viewLifetime == null || viewLifetime.IsTerminated) return "ViewLifetime was not alive in OnViewAdded";
                p.SetView(null);
                if (!viewLifetime.IsTerminated) return "SetView(null) did not end ViewLifetime";
                p.Close();
                return p.Lifetime.IsTerminated ? null : "Close() did not end the presenter's Lifetime";
            }
            finally
            {
                Destroy(go);
            }
        }

        // --- bookkeeping -----------------------------------------------------------------------------------------

        private void Check(string name, Func<string> body)
        {
            string failure;
            try
            {
                failure = body();
            }
            catch (Exception e)
            {
                failure = "threw " + Describe(e);
            }

            Record(name, failure);
        }

        private void Record(string name, string failure)
        {
            if (!_done.Add(name)) return;
            if (failure == null)
            {
                Debug.Log(Prefix + " check " + name + ": PASS");
            }
            else
            {
                _failures[name] = failure;
                Debug.Log(Prefix + " check " + name + ": FAIL " + OneLine(failure));
            }
        }

        // Everything still pending except context-behaviour, which builds a context of its own.
        private void Skip(string reason)
        {
            foreach (var name in Checks)
                if (!_done.Contains(name) && name != "context-behaviour")
                    Record(name, "not run: " + reason);
        }

        private void Finish(string reason)
        {
            if (_summary != null) return;
            foreach (var name in Checks)
                if (!_done.Contains(name))
                    Record(name, "not run: " + (reason ?? "the run ended first"));

            var failed = new List<string>();
            foreach (var name in Checks)
                if (_failures.ContainsKey(name))
                    failed.Add(name);

            _summary = failed.Count == 0
                ? Prefix + ": PASS " + Checks.Length + "/" + Checks.Length
                : Prefix + ": FAIL " + failed.Count + "/" + Checks.Length + " " + string.Join(",", failed);
            Debug.Log(_summary);

            var camera = Camera.main;
            if (camera != null)
                camera.backgroundColor = failed.Count == 0 ? new Color(0.1f, 0.45f, 0.2f) : new Color(0.6f, 0.12f, 0.12f);
#if UNITY_WEBGL && !UNITY_EDITOR
            OpenUGDSmokeSetTitle(_summary);
#endif
        }

        private static Exception Unwrap(AggregateException e) =>
            e == null ? null : (e.InnerExceptions.Count == 1 ? e.InnerExceptions[0] : e);

        private static string Describe(Exception e)
        {
            if (e == null) return "(no exception)";
            var text = new StringBuilder(e.GetType().Name + ": " + e.Message);
            for (var inner = e.InnerException; inner != null; inner = inner.InnerException)
                text.Append(" <- ").Append(inner.GetType().Name).Append(": ").Append(inner.Message);
            return text.ToString();
        }

        private static string OneLine(string text) => text.Replace("\r", " ").Replace("\n", " | ");
    }
}
