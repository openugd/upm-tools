// Must NOT compile: this assembly declares only com.openugd.signal, and OpenUGD.Lifetime lives in
// com.openugd.lifetime. See expect.json.
namespace OpenUGD.Tools.Canary
{
    internal static class TransitiveCanary
    {
        internal static object Probe(OpenUGD.Lifetime lifetime) => lifetime;
    }
}
