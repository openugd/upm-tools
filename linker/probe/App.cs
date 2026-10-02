// The linker gate's probe: a tiny "game" that uses com.openugd.context only through its public API, the
// way a user would. linker-gate.sh compiles it against the real package sources, strips it with Unity's
// own UnityLinker, inspects what survived and runs the stripped assemblies.
//
// Every scenario prints one "CHECK <name>=<True|False>" line; the gate expects all of them True.
// Nothing here may wrap a registration call in an unannotated generic helper: that is a user-side IL2091
// by design, and the gate must stay green once the package is fixed.
using System;
using System.Threading;
using System.Threading.Tasks;
using OpenUGD;

namespace Probe
{
    public interface ISave { }
    public interface ILocalization { string Get(string key); }

    // Implicit parameterless constructor, registered with Add<T>().
    public sealed class Clock { }

    // Greedy constructor with no attribute: the container's primary mode.
    public sealed class SaveService : ISave
    {
        public readonly Clock Clock;
        public SaveService(Clock clock) { Clock = clock; }
    }

    // Boot callback on a service built through a greedy constructor.
    public sealed class Profile : IAwakeService
    {
        public bool Awoken;
        public Profile(ISave save) { }
        public Task AwakeAsync(CancellationToken cancellationToken) { Awoken = true; return Task.CompletedTask; }
    }

    // [Inject] on a constructor must win over the parameterless one.
    public sealed class Marked
    {
        public readonly bool UsedInjectConstructor;
        public Marked() { }
        [Inject] public Marked(Clock clock) { UsedInjectConstructor = clock != null; }
    }

    // [Inject] on a public field, a property and a private field.
    public sealed class Members
    {
        [Inject] public Clock Field;
        [Inject] public ISave Property { get; set; }
        [Inject] private Clock _private = null;
        public Clock Private => _private;
    }

    // Optional member whose contract is not registered: the attribute's named argument must survive.
    public sealed class WithOptional
    {
        [Inject(Optional = true)] public ILocalization Localization { get; set; }
        [Inject] public Clock Clock { get; set; }
    }

    // Built by a factory, then member-injected.
    public sealed class FromFactory { [Inject] public Clock Injected { get; set; } }

    // Never registered: created with Instantiate<T>().
    public sealed class OneOff
    {
        public readonly Clock Clock;
        public OneOff(Clock clock) { Clock = clock; }
    }

    // Not built by the container at all (a MonoBehaviour stand-in): Inject(target).
    public sealed class ViewLike
    {
        [Inject] public Clock Clock { get; set; }
        [Inject] private ISave _save = null;
        public ISave Save => _save;
    }

    public static class Entry
    {
        static void Check(string name, bool ok) { Console.WriteLine("CHECK " + name + "=" + ok); }

        public static void Run()
        {
            try
            {
                RunAsync().GetAwaiter().GetResult();
                Console.WriteLine("DONE");
            }
            catch (Exception e)
            {
                Console.WriteLine("BUILD FAILED: " + e.GetType().Name + ": " + e.Message.Replace('\n', ' '));
            }
        }

        static async Task RunAsync()
        {
            var builder = Context.CreateBuilder();
            builder.Services.Add<Clock>();
            builder.Services.Add<SaveService>().As<ISave>();
            builder.Services.Add<Profile>();
            builder.Services.Add<Marked>();
            builder.Services.Add<Members>();
            builder.Services.Add<WithOptional>();
            builder.Services.Add(c => new FromFactory());
            var context = await builder.BuildAsync();

            Check("greedy-constructor", context.Resolve<SaveService>().Clock != null);
            Check("awake-boot", context.Resolve<Profile>().Awoken);
            Check("inject-constructor", context.Resolve<Marked>().UsedInjectConstructor);
            var m = context.Resolve<Members>();
            Check("inject-field", m.Field != null);
            Check("inject-property", m.Property != null);
            Check("inject-private-field", m.Private != null);
            var o = context.Resolve<WithOptional>();
            Check("inject-optional", o.Localization == null && o.Clock != null);
            Check("factory-member-injection", context.Resolve<FromFactory>().Injected != null);
            Check("instantiate-unregistered", context.Instantiate<OneOff>().Clock != null);
            var view = new ViewLike();
            context.Inject(view);
            Check("inject-existing-object", view.Clock != null && view.Save != null);
        }

        public static void Main() { Run(); }
    }
}
