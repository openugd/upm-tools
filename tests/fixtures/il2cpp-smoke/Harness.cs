// Runs the IL2CPP smoke player's engine-free checks (il2cpp-template/Assets/Il2CppSmoke/Checks.cs and Services.cs) on
// desktop Mono, in the order Boot.cs runs them and with Task continuations resumed on the calling thread, as Unity's
// synchronization context resumes them on the main thread. tests/test_il2cpp_smoke.py compiles this into Probe.exe,
// runs it as is and after UnityLinker (linker_gate.link with linker/probe/root.xml, which roots Probe.Entry.Run),
// and reads the "CHECK <name>=<True|False>" lines with linker_gate.run_probe.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using Il2CppSmoke;
using OpenUGD;

namespace Probe
{
    internal sealed class MainThreadContext : SynchronizationContext
    {
        private readonly BlockingCollection<KeyValuePair<SendOrPostCallback, object>> _queue =
            new BlockingCollection<KeyValuePair<SendOrPostCallback, object>>();

        public override void Post(SendOrPostCallback d, object state) =>
            _queue.Add(new KeyValuePair<SendOrPostCallback, object>(d, state));

        public override void Send(SendOrPostCallback d, object state) => d(state);

        public bool RunUntil(Task task, int timeoutMs)
        {
            var clock = Stopwatch.StartNew();
            while (!task.IsCompleted)
            {
                if (clock.ElapsedMilliseconds > timeoutMs) return false;
                KeyValuePair<SendOrPostCallback, object> item;
                if (_queue.TryTake(out item, 50)) item.Key(item.Value);
            }

            return true;
        }
    }

    public static class Entry
    {
        private static void Check(string name, Func<string> body)
        {
            string failure;
            try
            {
                failure = body();
            }
            catch (Exception e)
            {
                failure = "threw " + e.GetType().Name + ": " + e.Message;
            }

            Console.WriteLine("CHECK " + name + "=" + (failure == null));
            if (failure != null) Console.WriteLine("FAILED " + name + ": " + failure.Replace('\n', ' '));
        }

        public static void Run()
        {
            var sync = new MainThreadContext();
            SynchronizationContext.SetSynchronizationContext(sync);

            Check("lifetime-nesting", EngineFreeChecks.LifetimeNesting);
            Check("lifetime-terminated", EngineFreeChecks.LifetimeTerminated);
            Check("signal-struct-state", EngineFreeChecks.SignalStructState);
            Check("signal-generic-state", EngineFreeChecks.SignalGenericState);
            Check("signal-covariance", EngineFreeChecks.SignalCovariance);

            var scope = Lifetime.Eternal.DefineNested("harness");
            Context root = null;
            Check("context-build", () =>
            {
                var build = EngineFreeChecks.BuildRoot(scope.Lifetime);
                if (build.IsCompleted) return "BuildAsync completed synchronously; the Awake step's Task.Yield did not defer it";
                if (!sync.RunUntil(build, 10000)) return "BuildAsync did not complete within 10 s";
                root = build.GetAwaiter().GetResult();
                return root == null ? "BuildAsync returned null" : null;
            });
            if (root != null)
            {
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
                Check("presenter-attach-inject", c.PresenterAttachInject);
                Check("presenter-factory-create", c.PresenterFactoryCreate);
                Check("presenter-close", c.PresenterClose);
                Check("command-register-tell", c.CommandRegisterTell);
                Check("command-unregister", c.CommandUnregister);
                Check("context-dispose", c.ContextDispose);
            }

            scope.Terminate();
            Console.WriteLine("DONE");
        }

        public static void Main() { Run(); }
    }
}
