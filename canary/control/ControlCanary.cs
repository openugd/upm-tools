// Must compile: identical to the transitive canary, but com.openugd.lifetime is declared here.
// See expect.json.
namespace OpenUGD.Tools.Canary
{
    internal static class ControlCanary
    {
        internal static object Probe(OpenUGD.Lifetime lifetime) => lifetime;
    }
}
