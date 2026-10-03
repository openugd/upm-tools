// A component the container did not build: Boot.cs adds it at run time and fills it with Context.Inject.
// Each MonoBehaviour has a file of its own name, as Unity needs to find its script in a player.
using OpenUGD;
using UnityEngine;

namespace Il2CppSmoke
{
    public sealed class InjectTarget : MonoBehaviour
    {
        [Inject] public IClock Clock { get; set; }
        [Inject] private ISave _save = null;
        [Inject(Optional = true)] public ILocalization Localization { get; set; }
        public ISave Save => _save;
    }
}
