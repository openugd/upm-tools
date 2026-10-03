// A presenter whose view is a corelib ViewBehaviour, so the presenter path that needs the engine runs too. Kept out of
// Services.cs, which stays engine-free so the upm-tools tests can run it on desktop Mono.
using OpenUGD;
using OpenUGD.Presenters;

namespace Il2CppSmoke
{
    public sealed class HudView : ViewBehaviour
    {
    }

    // A presenter whose view is a MonoBehaviour (HudView): needs the engine, so the linker gate cannot run it.
    public sealed class HudViewPresenter : Presenter<HudView>
    {
        public int ViewAdded, Refreshed;
        public Lifetime CapturedViewLifetime;
        [Inject] public IClock Clock { get; set; }

        protected override void OnViewAdded()
        {
            ViewAdded++;
            CapturedViewLifetime = ViewLifetime;
        }

        protected override void OnRefresh() { Refreshed++; }
    }
}
