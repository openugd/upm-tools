using UnityEngine;

namespace UpmTools.Fixture.ModuleDeps.Editor
{
    /// <summary>
    /// Editor-only code may use any module: Unity compiles an Editor-only assembly against every engine module,
    /// whatever the project has installed, so this use of UnityEngine.AudioModule needs no declaration.
    /// </summary>
    public static class ListenerFocusProbe
    {
        /// <summary>The audio source on <paramref name="focus"/>, or null.</summary>
        /// <param name="focus">The component to look at.</param>
        /// <returns>The first <c>AudioSource</c> on the same game object.</returns>
        public static AudioSource SourceOf(ListenerFocus focus) => focus.GetComponent<AudioSource>();
    }
}
