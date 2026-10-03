// The smoke player's checks that need no engine: lifetimes, signals, the container, presenters without a view and
// commands. Boot.cs runs them in the player, interleaved with the checks that do need the engine. Each check
// returns null when it passes and the reason when it fails; an exception it throws is a failure too.
//
// This file and Services.cs reference no UnityEngine type, so upm-tools also compiles them with a console entry
// point and runs them on desktop Mono, before and after UnityLinker: that proves the expectations themselves before
// an IL2CPP player is built (tests/test_il2cpp_smoke.py).
//
// WebGL's default exception support catches only explicitly thrown exceptions, which is what a shipped game uses,
// so the checks test for null instead of dereferencing a value that stripping may have left unset.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Threading.Tasks;
using OpenUGD;
using OpenUGD.Commands;
using OpenUGD.Presenters;

namespace Il2CppSmoke
{
    public static class EngineFreeChecks
    {
        public static string LifetimeNesting()
        {
            var order = new List<string>();
            var outer = Lifetime.Eternal.DefineNested("outer");
            outer.Lifetime.AddAction(() => order.Add("outer-1"));
            var inner = outer.Lifetime.DefineNested("inner");
            inner.Lifetime.AddAction(() => order.Add("inner"));
            outer.Lifetime.AddAction(() => order.Add("outer-2"));
            var token = inner.Lifetime.AsCancellationToken();
            outer.Terminate();
            if (!inner.IsTerminated) return "terminating the outer scope did not end the nested one";
            if (!token.IsCancellationRequested) return "AsCancellationToken was not cancelled";
            var got = string.Join(",", order);
            // One LIFO sequence per lifetime, actions and nested scopes interleaved (the Lifetime docs).
            return got == "outer-2,inner,outer-1" ? null : "termination order was " + got + ", expected outer-2,inner,outer-1";
        }

        public static string LifetimeTerminated()
        {
            var scope = Lifetime.Eternal.DefineNested("ended");
            scope.Terminate();
            var ran = false;
            scope.Lifetime.AddAction(() => ran = true);
            if (!ran) return "an action added to a terminated lifetime did not run immediately";
            var nested = scope.Lifetime.DefineNested("late");
            return nested.IsTerminated ? null : "a scope nested in a terminated lifetime is alive";
        }

        public static string SignalStructState()
        {
            var owner = Lifetime.Eternal.DefineNested("signals");
            try
            {
                var signal = new DamageSignal(owner.Lifetime);
                var subscriber = owner.Lifetime.DefineNested("subscriber");
                var total = 0;
                string source = null;
                signal.Subscribe(subscriber.Lifetime, (amount, from) => { total += amount; source = from; });
                signal.Fire(5, "trap");
                subscriber.Terminate();
                signal.Fire(100, "after");
                if (source != "trap") return "the handler got source '" + source + "'";
                return total == 5 ? null : "handler total was " + total + ", expected 5 (one fire before the subscriber ended)";
            }
            finally
            {
                owner.Terminate();
            }
        }

        public static string SignalGenericState()
        {
            var owner = Lifetime.Eternal.DefineNested("signal3");
            try
            {
                var signal = new Signal3<int, float, long>(owner.Lifetime);
                var got = "";
                signal.Subscribe(owner.Lifetime,
                    (a, b, c) => got = string.Format(CultureInfo.InvariantCulture, "{0}|{1:0.0}|{2}", a, b, c));
                signal.Fire(1, 2.5f, 3L);
                return got == "1|2.5|3" ? null : "the handler got '" + got + "'";
            }
            finally
            {
                owner.Terminate();
            }
        }

        public static string SignalCovariance()
        {
            var owner = Lifetime.Eternal.DefineNested("covariance");
            try
            {
                var strings = new Signal<string>(owner.Lifetime);
                object boxed = strings;
                if (!(boxed is ISignal<object>)) return "Signal<string> is not an ISignal<object> at run time";
                var objects = (ISignal<object>)boxed;
                object got = null;
                objects.Subscribe(owner.Lifetime, value => got = value);
                strings.Fire("covariant");
                return "covariant".Equals(got) ? null : "a handler subscribed through ISignal<object> got " + (got ?? "nothing");
            }
            finally
            {
                owner.Terminate();
            }
        }

        /// The root context every ContextChecks method works on. Boot passes PlaySession.Lifetime, as the context
        /// README recommends for a root context.
        public static Task<Context> BuildRoot(Lifetime lifetime)
        {
            var builder = Context.CreateBuilder(lifetime);
            builder.Services.Add<Clock>().As<IClock>();
            builder.Services.Add<SaveService>().As<ISave>();
            builder.Services.Add<Greedy>();
            builder.Services.Add<Marked>();
            builder.Services.Add<Members>();
            builder.Services.Add<Optionals>();
            builder.Services.Add<Booted>();
            builder.Services.Add<TickA>().AsElementOf<ITickable>();
            builder.Services.Add<TickB>().AsElementOf<ITickable>();
            builder.Services.Add<TickLoop>();
            builder.Services.Add<DebugMenu>();
            builder.Services.AddCommandMap();
            builder.Services.Add<ContextPresenterFactory>().As<IPresenterFactory>();
            return builder.BuildAsync();
        }
    }

    /// The checks on a root context built by EngineFreeChecks.BuildRoot, meant to run in declaration order: the
    /// presenter and command checks carry state from one to the next, and ContextDispose ends the context.
    public sealed class ContextChecks
    {
        private readonly Context _root;
        private readonly Lifetime.Definition _presenterScope;
        private readonly ContextPresenterFactory _factory;
        private HudPresenter _hud;
        private Lifetime.Definition _registration;

        public ContextChecks(Context root)
        {
            _root = root;
            _presenterScope = root.Lifetime.DefineNested("presenters");
            _factory = new ContextPresenterFactory(root);
        }

        /// The presenter root PresenterAttachInject creates, for checks that attach more presenters under it.
        public Presenter.Root Presenters { get; private set; }

        public string ConstructorImplicit() =>
            _root.Resolve<IClock>() is Clock ? null : "IClock did not resolve to Clock";

        public string ConstructorGreedy()
        {
            var g = _root.Resolve<Greedy>();
            if (g.Width != 2) return "used the constructor with " + g.Width + " parameter(s); the widest takes 2";
            return g.Clock != null && g.Save != null ? null : "the widest constructor got null arguments";
        }

        public string ConstructorInject()
        {
            var used = _root.Resolve<Marked>().Used;
            return used == "inject" ? null : "used the " + used + " constructor instead of the [Inject] one";
        }

        public string InjectField() => _root.Resolve<Members>().Field != null ? null : "[Inject] public field left null";

        public string InjectProperty() => _root.Resolve<Members>().Property != null ? null : "[Inject] property left null";

        public string InjectPrivateField() =>
            _root.Resolve<Members>().Private != null ? null : "[Inject] private field left null";

        public string InjectOptional()
        {
            var o = _root.Resolve<Optionals>();
            if (o.Clock == null) return "the required [Inject] property next to the optional ones was left null";
            if (o.Localization != null) return "an optional member with no registration was set";
            return o.Fallback == FallbackLocalization.Instance ? null
                : "an optional field lost its initializer: " + (o.Fallback == null ? "null" : o.Fallback.GetType().Name);
        }

        public string BootAwakeInitialize()
        {
            var b = _root.Resolve<Booted>();
            if (!b.Awoken) return "AwakeAsync did not run";
            if (!b.AwokeAfterYield) return "AwakeAsync did not resume after await Task.Yield()";
            if (!b.Initialized) return "InitializeAsync did not run";
            if (!b.InitializedAfterAwake) return "InitializeAsync ran before AwakeAsync finished";
            return b.SameThread ? null : "AwakeAsync resumed on another thread";
        }

        public string CollectionElements()
        {
            var items = _root.Resolve<TickLoop>().Items;
            if (items == null) return "TickLoop got a null IReadOnlyList<ITickable>";
            var names = new StringBuilder();
            for (var i = 0; i < items.Count; i++) names.Append(items[i] == null ? "null" : items[i].Name);
            if (names.ToString() != "AB") return "got [" + names + "], expected [AB] in registration order";
            if (!(items[1] is TickB tb) || tb.Clock == null) return "TickB was not constructed with its IClock";
            var resolved = _root.Resolve<IReadOnlyList<ITickable>>();
            return resolved != null && resolved.Count == 2 && ReferenceEquals(resolved[0], items[0])
                ? null : "Resolve<IReadOnlyList<ITickable>>() differs from the list TickLoop got";
        }

        public string CollectionEmpty()
        {
            var panels = _root.Resolve<DebugMenu>().Panels;
            if (panels == null) return "DebugMenu got a null list";
            if (panels.Count != 0) return "DebugMenu got " + panels.Count + " panel(s), expected none";
            var resolved = _root.Resolve<IReadOnlyList<IDebugPanel>>();
            return resolved != null && resolved.Count == 0 ? null : "Resolve<IReadOnlyList<IDebugPanel>>() was not empty";
        }

        public string ChildContext()
        {
            var builder = Context.CreateBuilder(parent: _root);
            builder.Services.Add<ChildClock>().As<IClock>();
            builder.Services.Add<LevelService>();
            var child = builder.Build();
            var level = child.Resolve<LevelService>();
            if (child.Parent != _root) return "child.Parent is not the root";
            if (!(level.Clock is ChildClock)) return "the child's IClock did not shadow the parent's";
            if (!ReferenceEquals(level.Save, _root.Resolve<ISave>())) return "the child did not hand out the parent's ISave";
            if (!(_root.Resolve<IClock>() is Clock)) return "the child's registration leaked into the parent";
            child.Dispose();
            if (!level.Disposed) return "disposing the child did not dispose its LevelService";
            if (!child.Lifetime.IsTerminated) return "the child's Lifetime is alive after Dispose";
            if (_root.Lifetime.IsTerminated) return "disposing the child ended the root";
            return ((SaveService)_root.Resolve<ISave>()).Disposed ? "disposing the child disposed the parent's SaveService" : null;
        }

        public string InstantiateUnregistered()
        {
            var one = _root.Instantiate<OneOff>();
            if (one == null || one.Clock == null || one.Save == null) return "Instantiate<OneOff>() did not fill its constructor";
            var tagged = _root.Instantiate<Tagged>("from-args");
            return tagged != null && tagged.Tag == "from-args" && tagged.Clock != null ? null
                : "Instantiate<Tagged>(args) did not combine the argument with the context";
        }

        public string PresenterAttachInject()
        {
            Presenters = new Presenter.Root(_presenterScope.Lifetime, _factory);
            _hud = Presenters.AddPresenter(new HudPresenter());
            if (!_hud.InjectedBeforeInitialize) return "[Inject] members were not filled before OnInitialize";
            return _hud.Save != null ? null : "[Inject] private field left null";
        }

        public string PresenterFactoryCreate()
        {
            if (Presenters == null) return "no Presenter.Root (presenter-attach-inject failed)";
            var shop = Presenters.AddPresenter((ShopPresenter)_factory.Create(typeof(ShopPresenter)));
            if (shop.Save == null) return "Create(typeof(ShopPresenter)) did not fill the greedy constructor";
            if (shop.Clock == null) return "Create(typeof(ShopPresenter)) left the [Inject] property null";
            var registered = _root.Resolve<IPresenterFactory>();
            if (!(registered is ContextPresenterFactory)) return "IPresenterFactory did not resolve to ContextPresenterFactory";
            var marked = Presenters.AddPresenter((MarkedPresenter)registered.Create(typeof(MarkedPresenter)));
            return marked.UsedInjectConstructor ? null : "the resolved factory did not use MarkedPresenter's [Inject] constructor";
        }

        public string PresenterClose()
        {
            if (_hud == null) return "no HudPresenter (presenter-attach-inject failed)";
            _presenterScope.Terminate();
            if (!_hud.Lifetime.IsTerminated) return "ending the scope did not end the child presenter";
            return _hud.Closed ? null : "OnClose was not called";
        }

        public string CommandRegisterTell()
        {
            _registration = _root.MapCommand().Map<Buy>().RegisterCommand<BuyCommand>();
            BuyCommand.Reset();
            _root.Tell(new Buy { Amount = 3 });
            if (BuyCommand.Executions != 1) return "Tell ran the command " + BuyCommand.Executions + " time(s)";
            if (BuyCommand.Amount != 3) return "the command got the wrong message";
            if (!BuyCommand.ExecutionAlive) return "the execution Lifetime was not alive in Execute";
            if (!BuyCommand.HadSave) return "the greedy constructor got a null ISave";
            return BuyCommand.Injected ? null : "[Inject] property on the command left null";
        }

        public string CommandUnregister()
        {
            if (_registration == null) return "no registration (command-register-tell failed)";
            _registration.Terminate();
            _root.Tell(new Buy { Amount = 4 });
            return BuyCommand.Executions == 1 ? null : "the command still ran after its registration ended";
        }

        public string ContextDispose()
        {
            var save = (SaveService)_root.Resolve<ISave>();
            _root.Dispose();
            if (!_root.Lifetime.IsTerminated) return "Dispose() did not end the context's Lifetime";
            if (!save.Disposed) return "Dispose() did not dispose SaveService";
            try
            {
                _root.Resolve<IClock>();
                return "Resolve after Dispose did not throw";
            }
            catch (ObjectDisposedException)
            {
                return null;
            }
        }
    }
}
