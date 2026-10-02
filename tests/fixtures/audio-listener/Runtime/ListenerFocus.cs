using UnityEngine;

namespace UpmTools.Fixture.ModuleDeps
{
    /// <summary>
    /// The pattern of corelib's ContextInstanceComponent: a scene object that owns an audio listener and a canvas
    /// and switches them with its focus. Each type comes from a different place:
    /// <c>MonoBehaviour</c> and <c>GameObject</c> from UnityEngine.CoreModule (every project has it),
    /// <c>Canvas</c> from UnityEngine.UIModule (com.unity.modules.ui, which com.unity.ugui depends on), and
    /// <c>AudioListener</c> from UnityEngine.AudioModule (only with com.unity.modules.audio).
    /// </summary>
    public sealed class ListenerFocus : MonoBehaviour
    {
        [SerializeField] private AudioListener _listener;
        [SerializeField] private Canvas _canvas;

        /// <summary>Enables or disables the listener and the canvas together.</summary>
        /// <param name="focused">True to give this object the focus.</param>
        public void SetFocus(bool focused)
        {
            _listener.enabled = focused;
            _canvas.enabled = focused;
            gameObject.name = focused ? "focused" : "unfocused";
        }
    }
}
