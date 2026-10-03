// The corelib half of the linker gate's probe: commands and presenters, used through their public API the way a
// game uses them. linker-gate.sh compiles this file with PROBE_CORELIB defined unless --without-corelib is passed;
// App.cs calls Register before the context is built and Run after.
//
// Every scenario prints one "CHECK <name>=<True|False>" line. A scenario that throws prints its CHECK lines as False
// and one "FAILED <scenario>: <exception>" line, so a stripped constructor shows up as the container's own message.
// The same rule as App.cs applies: no registration call is wrapped in an unannotated generic helper.
#if PROBE_CORELIB
using System;
using OpenUGD;
using OpenUGD.Commands;
using OpenUGD.Presenters;

namespace Probe
{
    // --- commands ------------------------------------------------------------------------------------------------

    public sealed class Buy : IMessage { public int Amount; }
    public sealed class Sell : IMessage { }
    public sealed class Ping : IMessage { }

    // Registered with RegisterCommand<T>(): a greedy constructor that takes the message, the execution's Lifetime
    // and a service, plus [Inject] members filled in after construction.
    public sealed class BuyCommand : ICommand
    {
        public static bool Constructed, Executed, Injected;

        private readonly Buy _message;
        private readonly Lifetime _execution;
        private readonly ISave _save;

        public BuyCommand(Buy message, Lifetime execution, ISave save)
        {
            _message = message;
            _execution = execution;
            _save = save;
            Constructed = true;
        }

        [Inject] public Clock Clock { get; set; }
        [Inject] private ISave _injected = null;

        public void Execute()
        {
            Executed = _message != null && _message.Amount == 3 && _execution != null && !_execution.IsTerminated &&
                       _save != null;
            Injected = Clock != null && _injected != null;
        }
    }

    // Mapped with Map<TMessage, TCommand>(): the [Inject] constructor must win over the parameterless one.
    public sealed class SellCommand : ICommand
    {
        public static bool UsedInjectConstructor;

        private readonly bool _marked;

        public SellCommand() { }
        [Inject] public SellCommand(Sell message, Clock clock) { _marked = message != null && clock != null; }

        public void Execute() { UsedInjectConstructor = _marked; }
    }

    // Mapped with a factory: no reflection at all, the control case.
    public sealed class PingCommand : ICommand
    {
        public static bool Executed;

        private readonly Ping _message;

        public PingCommand(Ping message) { _message = message; }

        public void Execute() { Executed = _message != null; }
    }

    // --- presenters ----------------------------------------------------------------------------------------------

    // Built with new and attached under a root: ContextPresenterFactory.Inject fills the [Inject] members before
    // OnInitialize.
    public sealed class HudPresenter : Presenter
    {
        public bool InjectedBeforeInitialize;

        [Inject] public Clock Clock { get; set; }
        [Inject] private ISave _save = null;

        public ISave Save => _save;

        protected override void OnInitialize() { InjectedBeforeInitialize = Clock != null && _save != null; }
    }

    // Built by ContextPresenterFactory.Create(typeof(ShopPresenter)): a greedy constructor and an [Inject] member.
    public sealed class ShopPresenter : Presenter
    {
        public readonly ISave Save;

        public ShopPresenter(ISave save) { Save = save; }

        [Inject] public Clock Clock { get; set; }
    }

    // Built by Create(Type): the [Inject] constructor must win over the parameterless one.
    public sealed class MarkedPresenter : Presenter
    {
        public readonly bool UsedInjectConstructor;

        public MarkedPresenter() { }
        [Inject] public MarkedPresenter(Clock clock) { UsedInjectConstructor = clock != null; }
    }

    public static class CorelibProbe
    {
        // Before BuildAsync: the command map, and the factory registered as a service the way the
        // ContextPresenterFactory docs show.
        public static void Register(ServiceCollection services)
        {
            services.AddCommandMap();
            services.Add<ContextPresenterFactory>().As<IPresenterFactory>();
        }

        public static void Run(Context context)
        {
            RunPresenters(context);
            RunCommands(context);
        }

        static void RunCommands(Context context)
        {
            IMapCommand map = null;
            Scenario("command map", new[] { "command-register-generic", "command-inject-members",
                "command-map-inject-constructor", "command-factory" }, () => map = context.MapCommand());
            if (map == null) return;

            // One scenario per registration verb, so a stripped constructor fails its own checks only.
            Scenario("RegisterCommand<BuyCommand>", new[] { "command-register-generic", "command-inject-members" }, () =>
            {
                map.Map<Buy>().RegisterCommand<BuyCommand>();
                context.Tell(new Buy { Amount = 3 });
                Entry.Check("command-register-generic", BuyCommand.Constructed && BuyCommand.Executed);
                Entry.Check("command-inject-members", BuyCommand.Injected);
            });
            Scenario("Map<Sell, SellCommand>", new[] { "command-map-inject-constructor" }, () =>
            {
                map.Map<Sell, SellCommand>();
                context.Tell(new Sell());
                Entry.Check("command-map-inject-constructor", SellCommand.UsedInjectConstructor);
            });
            Scenario("Map<Ping>(factory)", new[] { "command-factory" }, () =>
            {
                map.Map<Ping>((message, lifetime) => new PingCommand(message));
                context.Tell(new Ping());
                Entry.Check("command-factory", PingCommand.Executed);
            });
        }

        static void RunPresenters(Context context)
        {
            ContextPresenterFactory factory = null;
            Presenter.Root root = null;
            Scenario("Presenter.Root", new[] { "presenter-new-inject", "presenter-create-constructor",
                "presenter-create-inject-constructor" }, () =>
            {
                factory = new ContextPresenterFactory(context);
                root = new Presenter.Root(context.Lifetime, factory);
            });

            if (root != null)
            {
                // One scenario per way a presenter is built, so a stripped constructor fails its own check only.
                Scenario("AddPresenter(new HudPresenter())", new[] { "presenter-new-inject" }, () =>
                {
                    var hud = root.AddPresenter(new HudPresenter());
                    Entry.Check("presenter-new-inject", hud.InjectedBeforeInitialize && hud.Save != null);
                });
                Scenario("Create(typeof(ShopPresenter))", new[] { "presenter-create-constructor" }, () =>
                {
                    var shop = root.AddPresenter((ShopPresenter)factory.Create(typeof(ShopPresenter)));
                    Entry.Check("presenter-create-constructor", shop.Save != null && shop.Clock != null);
                });
                Scenario("Create(typeof(MarkedPresenter))", new[] { "presenter-create-inject-constructor" }, () =>
                {
                    var marked = root.AddPresenter((MarkedPresenter)factory.Create(typeof(MarkedPresenter)));
                    Entry.Check("presenter-create-inject-constructor", marked.UsedInjectConstructor);
                });
                root.Close();
            }

            Scenario("Resolve<IPresenterFactory>()", new[] { "presenter-factory-registered" }, () =>
            {
                var registered = context.Resolve<IPresenterFactory>();
                var shop = (ShopPresenter)registered.Create(typeof(ShopPresenter));
                Entry.Check("presenter-factory-registered",
                    registered is ContextPresenterFactory && shop.Save != null && shop.Clock != null);
            });
        }

        static void Scenario(string name, string[] checks, Action body)
        {
            try
            {
                body();
            }
            catch (Exception e)
            {
                Console.WriteLine("FAILED " + name + ": " + e.GetType().Name + ": " + e.Message.Replace('\n', ' '));
                foreach (var check in checks) Entry.Check(check, false);
            }
        }
    }
}
#endif
