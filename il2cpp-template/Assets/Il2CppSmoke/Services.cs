// The plain C# types the smoke player's checks use: services, presenters, commands and signals, each written
// the way a game writes them and reached only through the packages' public API. Checks.cs and Boot.cs run the
// checks. Engine-free, like Checks.cs: the one presenter with a MonoBehaviour view lives in HudView.cs.
//
// Nothing here wraps a registration call in an unannotated generic helper of its own (that is a user-side IL2091
// by design, see the context README); every type reaches the container through Add<T>(), Instantiate<T>(),
// RegisterCommand<T>() or ContextPresenterFactory.Create(typeof(T)) directly.
using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using OpenUGD;
using OpenUGD.Commands;
using OpenUGD.Presenters;

namespace Il2CppSmoke
{
    // --- context: constructors ---------------------------------------------------------------------------------

    public interface IClock { int Ticks { get; } }

    // Implicit parameterless constructor, registered with Add<Clock>().As<IClock>().
    public sealed class Clock : IClock { public int Ticks => 1; }

    public interface ISave { }

    // Greedy constructor without an attribute; disposed with the context that built it.
    public sealed class SaveService : ISave, IDisposable
    {
        public readonly IClock Clock;
        public bool Disposed;
        public SaveService(IClock clock) { Clock = clock; }
        public void Dispose() { Disposed = true; }
    }

    // Three public constructors: the container must pick the widest one it can satisfy.
    public sealed class Greedy
    {
        public readonly int Width;
        public readonly IClock Clock;
        public readonly ISave Save;
        public Greedy() { Width = 0; }
        public Greedy(IClock clock) { Width = 1; Clock = clock; }
        public Greedy(IClock clock, ISave save) { Width = 2; Clock = clock; Save = save; }
    }

    // [Inject] on a constructor wins over the parameterless one and over a wider one without the attribute.
    public sealed class Marked
    {
        public readonly string Used;
        public Marked() { Used = "parameterless"; }
        [Inject] public Marked(IClock clock) { Used = clock != null ? "inject" : "inject(null)"; }
        public Marked(IClock clock, ISave save) { Used = "widest"; }
    }

    // --- context: members ----------------------------------------------------------------------------------------

    public sealed class Members
    {
        [Inject] public IClock Field;
        [Inject] public ISave Property { get; set; }
        [Inject] private IClock _private = null;
        public IClock Private => _private;
    }

    // Never registered anywhere: the optional members below must stay as they were.
    public interface ILocalization { string Get(string key); }

    public sealed class FallbackLocalization : ILocalization
    {
        public static readonly FallbackLocalization Instance = new FallbackLocalization();
        public string Get(string key) => key;
    }

    public sealed class Optionals
    {
        [Inject(Optional = true)] public ILocalization Localization { get; set; }
        [Inject(Optional = true)] private ILocalization _fallback = FallbackLocalization.Instance;
        [Inject] public IClock Clock { get; set; }
        public ILocalization Fallback => _fallback;
    }

    // --- context: boot -------------------------------------------------------------------------------------------

    // Both boot phases. AwakeAsync really awaits: Task.Yield resumes on Unity's synchronization context on a later
    // frame, so BuildAsync does not complete synchronously and the async state machines run under AOT.
    public sealed class Booted : IAwakeService, IInitializeService
    {
        public readonly ISave Save;
        public bool Awoken, AwokeAfterYield, Initialized, InitializedAfterAwake, SameThread;

        public Booted(ISave save) { Save = save; }

        public async Task AwakeAsync(CancellationToken cancellationToken)
        {
            var thread = Thread.CurrentThread.ManagedThreadId;
            Awoken = true;
            await Task.Yield();
            AwokeAfterYield = !cancellationToken.IsCancellationRequested;
            SameThread = thread == Thread.CurrentThread.ManagedThreadId;
        }

        public Task InitializeAsync(CancellationToken cancellationToken)
        {
            Initialized = true;
            InitializedAfterAwake = AwokeAfterYield;
            return Task.CompletedTask;
        }
    }

    // --- context: collections (AsElementOf + IReadOnlyList<T>) ---------------------------------------------------

    public interface ITickable { string Name { get; } }

    public sealed class TickA : ITickable { public string Name => "A"; }

    public sealed class TickB : ITickable
    {
        public readonly IClock Clock;
        public TickB(IClock clock) { Clock = clock; }
        public string Name => "B";
    }

    public sealed class TickLoop
    {
        public readonly IReadOnlyList<ITickable> Items;
        public TickLoop(IReadOnlyList<ITickable> items) { Items = items; }
    }

    // Nothing contributes an IDebugPanel: the list must be empty, not an error. The runtime has to create an
    // IDebugPanel[] that no code ever creates statically.
    public interface IDebugPanel { }

    public sealed class DebugMenu
    {
        public readonly IReadOnlyList<IDebugPanel> Panels;
        public DebugMenu(IReadOnlyList<IDebugPanel> panels) { Panels = panels; }
    }

    // --- context: child scope and one-off objects ----------------------------------------------------------------

    // Registered in the child as IClock: shadows the parent's Clock there only.
    public sealed class ChildClock : IClock { public int Ticks => 7; }

    public sealed class LevelService : IDisposable
    {
        public readonly IClock Clock;
        public readonly ISave Save;
        public bool Disposed;
        public LevelService(IClock clock, ISave save) { Clock = clock; Save = save; }
        public void Dispose() { Disposed = true; }
    }

    // Never registered: built with Context.Instantiate<T>().
    public sealed class OneOff
    {
        public readonly IClock Clock;
        public readonly ISave Save;
        public OneOff(IClock clock, ISave save) { Clock = clock; Save = save; }
    }

    // Never registered: built with Instantiate<T>(args), one argument supplied by the caller.
    public sealed class Tagged
    {
        public readonly string Tag;
        public readonly IClock Clock;
        public Tagged(string tag, IClock clock) { Tag = tag; Clock = clock; }
    }

    // --- presenters ----------------------------------------------------------------------------------------------

    // Built with new and attached: ContextPresenterFactory.Inject fills the [Inject] members before OnInitialize.
    public sealed class HudPresenter : Presenter
    {
        public bool InjectedBeforeInitialize, Closed;
        [Inject] public IClock Clock { get; set; }
        [Inject] private ISave _save = null;
        public ISave Save => _save;
        protected override void OnInitialize() { InjectedBeforeInitialize = Clock != null && _save != null; }
        protected override void OnClose() { Closed = true; }
    }

    // Built by ContextPresenterFactory.Create(typeof(ShopPresenter)): greedy constructor plus an [Inject] member.
    public sealed class ShopPresenter : Presenter
    {
        public readonly ISave Save;
        public ShopPresenter(ISave save) { Save = save; }
        [Inject] public IClock Clock { get; set; }
    }

    // Built by Create(Type) through the factory resolved from the container: the [Inject] constructor must win.
    public sealed class MarkedPresenter : Presenter
    {
        public readonly bool UsedInjectConstructor;
        public MarkedPresenter() { }
        [Inject] public MarkedPresenter(IClock clock) { UsedInjectConstructor = clock != null; }
    }

    // --- commands ------------------------------------------------------------------------------------------------

    public sealed class Buy : IMessage { public int Amount; }

    // Registered with RegisterCommand<BuyCommand>(): a greedy constructor taking the message, the execution's
    // Lifetime and a service, plus an [Inject] member filled in after construction.
    public sealed class BuyCommand : ICommand
    {
        public static int Executions, Amount;
        public static bool ExecutionAlive, HadSave, Injected;

        private readonly Buy _message;
        private readonly Lifetime _execution;
        private readonly ISave _save;

        public BuyCommand(Buy message, Lifetime execution, ISave save)
        {
            _message = message;
            _execution = execution;
            _save = save;
        }

        [Inject] public IClock Clock { get; set; }

        public static void Reset()
        {
            Executions = Amount = 0;
            ExecutionAlive = HadSave = Injected = false;
        }

        public void Execute()
        {
            Executions++;
            Amount = _message != null ? _message.Amount : -1;
            ExecutionAlive = _execution != null && !_execution.IsTerminated;
            HadSave = _save != null;
            Injected = Clock != null;
        }
    }

    // --- signals -------------------------------------------------------------------------------------------------

    // The state handed to SignalBase.Dispatch<TState> is a struct of the game's own.
    public struct DamageState
    {
        public int Amount;
        public string Source;
    }

    public sealed class DamageSignal : SignalBase<Action<int, string>>
    {
        public DamageSignal(Lifetime lifetime) : base(lifetime) { }

        public void Fire(int amount, string source) =>
            Dispatch(new DamageState { Amount = amount, Source = source },
                static (handler, state) => handler(state.Amount, state.Source));
    }

    // The three-argument signal from the SignalBase documentation: a generic class whose Dispatch state is a
    // ValueTuple of its own type parameters, instantiated over value types by Boot.cs.
    public sealed class Signal3<T1, T2, T3> : SignalBase<Action<T1, T2, T3>>
    {
        public Signal3(Lifetime lifetime) : base(lifetime) { }

        public void Fire(T1 a, T2 b, T3 c) =>
            Dispatch((a, b, c), static (handler, args) => handler(args.Item1, args.Item2, args.Item3));
    }
}
