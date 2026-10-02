// Must compile with no declared references: UnityEngine.UI is referenced implicitly by Unity.
namespace OpenUGD.Tools.Canary
{
    internal static class AutoRefCanary
    {
        internal static System.Type Probe() => typeof(UnityEngine.UI.Image);
    }
}
