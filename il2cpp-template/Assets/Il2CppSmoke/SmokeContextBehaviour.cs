// corelib's ContextBehaviour, subclassed the way the Bootstrap sample does: it builds its own context from Awake,
// on its own Lifetime, and exposes the Unity loop as signals. Boot.cs adds it at run time and waits for Startup.
using System.Threading;
using System.Threading.Tasks;
using OpenUGD;
using OpenUGD.Core;
using OpenUGD.Utils;

namespace Il2CppSmoke
{
    public sealed class SmokeContextBehaviour : ContextBehaviour
    {
        public bool Started;
        public int Updates;

        protected override bool PersistAcrossScenes => false;

        protected override Task<Context> CreateContextAsync(CancellationToken cancellationToken)
        {
            var builder = Context.CreateBuilder(Lifetime);
            builder.Services.AddInstance<ICoroutineProvider>(this);
            builder.Services.Add<Clock>().As<IClock>();
            builder.Services.Add<SaveService>().As<ISave>();
            builder.Services.Add<Booted>();
            return builder.BuildAsync(cancellationToken);
        }

        protected override void OnStarted(Context context)
        {
            Started = true;
            OnUpdate.Subscribe(context.Lifetime, () => Updates++);
        }
    }
}
