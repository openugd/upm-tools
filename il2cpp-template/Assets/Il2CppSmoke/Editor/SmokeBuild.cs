// The smoke project's build entry point, run by il2cpp-smoke.sh in upm-tools:
//
//   Unity -batchmode -nographics -projectPath <project> -buildTarget WebGL
//         -executeMethod Il2CppSmoke.Editor.SmokeBuild.Run
//         -smokeStripping Medium|High -smokeOutput <build folder> -smokeResult <result.json>
//
// It (re)creates Assets/Scenes/Boot.unity (a camera and the Boot component), configures a release WebGL player
// (IL2CPP, the requested managed stripping level, compression Disabled so a plain static server works, no data
// caching), builds it, writes a small JSON result for the script to read and exits with 0 only when the build
// succeeded. Any exception, a missing argument or a failed build exits with 1.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Il2CppSmoke.Editor
{
    public static class SmokeBuild
    {
        public const string ScenePath = "Assets/Scenes/Boot.unity";

        public static void Run()
        {
            var result = new Dictionary<string, object>();
            var settings = new Dictionary<string, object>();
            var errors = new List<object>();
            result["settings"] = settings;
            result["errors"] = errors;
            result["unity"] = Application.unityVersion;
            string resultPath = null;
            var exit = 1;
            try
            {
                var args = Environment.GetCommandLineArgs();
                resultPath = Arg(args, "-smokeResult");
                var output = Arg(args, "-smokeOutput") ?? throw new ArgumentException("-smokeOutput is required");
                var levelName = Arg(args, "-smokeStripping") ?? "Medium";
                var level = (ManagedStrippingLevel)Enum.Parse(typeof(ManagedStrippingLevel), levelName, true);
                result["output"] = output;

                if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.WebGL &&
                    !EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.WebGL, BuildTarget.WebGL))
                    throw new InvalidOperationException("could not switch the active build target to WebGL");

                CreateBootScene();
                Configure(level, settings);

                var options = new BuildPlayerOptions
                {
                    scenes = new[] { ScenePath },
                    locationPathName = output,
                    target = BuildTarget.WebGL,
                    targetGroup = BuildTargetGroup.WebGL,
                    options = BuildOptions.None,
                };
                var report = BuildPipeline.BuildPlayer(options);
                var summary = report.summary;
                result["result"] = summary.result.ToString();
                result["totalSeconds"] = summary.totalTime.TotalSeconds;
                result["totalSize"] = (long)summary.totalSize;
                result["totalErrors"] = summary.totalErrors;
                result["totalWarnings"] = summary.totalWarnings;
                var steps = new List<object>();
                foreach (var step in report.steps)
                {
                    steps.Add(new Dictionary<string, object>
                    {
                        { "name", step.name }, { "seconds", step.duration.TotalSeconds }, { "depth", step.depth },
                    });
                    foreach (var message in step.messages)
                    {
                        if ((message.type == LogType.Error || message.type == LogType.Exception) && errors.Count < 50)
                            errors.Add(step.name + ": " + message.content);
                    }
                }

                result["steps"] = steps;
                exit = summary.result == BuildResult.Succeeded ? 0 : 1;
            }
            catch (Exception e)
            {
                result["result"] = "Exception";
                result["exception"] = e.ToString();
                Debug.LogException(e);
            }
            finally
            {
                result["exit"] = exit;
                if (resultPath != null)
                {
                    try
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(resultPath)));
                        File.WriteAllText(resultPath, Json(result) + "\n");
                    }
                    catch (Exception e)
                    {
                        Debug.LogException(e);
                        exit = 1;
                    }
                }

                Debug.Log("SmokeBuild: exit " + exit);
                EditorApplication.Exit(exit);
            }
        }

        private static void CreateBootScene()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var cameraObject = new GameObject("Main Camera") { tag = "MainCamera" };
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.12f, 0.12f, 0.14f);
            new GameObject("Boot").AddComponent<Boot>();
            if (!EditorSceneManager.SaveScene(scene, ScenePath))
                throw new IOException("could not save " + ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
        }

        private static void Configure(ManagedStrippingLevel level, Dictionary<string, object> settings)
        {
            var target = NamedBuildTarget.WebGL;
            PlayerSettings.companyName = "OpenUGD";
            PlayerSettings.productName = "OpenUGD IL2CPP Smoke";
            PlayerSettings.SetScriptingBackend(target, ScriptingImplementation.IL2CPP);
            PlayerSettings.SetManagedStrippingLevel(target, level);
            PlayerSettings.SetIl2CppCompilerConfiguration(target, Il2CppCompilerConfiguration.Release);
            EditorUserBuildSettings.development = false;
            PlayerSettings.runInBackground = true;
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Disabled;
            PlayerSettings.WebGL.decompressionFallback = false;
            PlayerSettings.WebGL.dataCaching = false;
            PlayerSettings.WebGL.nameFilesAsHashes = false;
            PlayerSettings.WebGL.template = "APPLICATION:Default";
            // One console line per message: the checks print one line each.
            PlayerSettings.SetStackTraceLogType(LogType.Log, StackTraceLogType.None);
            PlayerSettings.SetStackTraceLogType(LogType.Warning, StackTraceLogType.None);
            var optimization = SetCodeOptimization("BuildTimes");

            settings["scriptingBackend"] = PlayerSettings.GetScriptingBackend(target).ToString();
            settings["managedStrippingLevel"] = PlayerSettings.GetManagedStrippingLevel(target).ToString();
            settings["stripEngineCode"] = PlayerSettings.stripEngineCode;
            settings["il2cppCompilerConfiguration"] = PlayerSettings.GetIl2CppCompilerConfiguration(target).ToString();
            settings["il2cppCodeGeneration"] = PlayerSettings.GetIl2CppCodeGeneration(target).ToString();
            settings["development"] = EditorUserBuildSettings.development;
            settings["compressionFormat"] = PlayerSettings.WebGL.compressionFormat.ToString();
            settings["exceptionSupport"] = PlayerSettings.WebGL.exceptionSupport.ToString();
            settings["codeOptimization"] = optimization;
            settings["apiCompatibilityLevel"] = PlayerSettings.GetApiCompatibilityLevel(target).ToString();
        }

        // UnityEditor.WebGL.UserBuildSettings.codeOptimization lives in the WebGL module's own editor assembly, which
        // an editor script does not reference; set it by reflection and report what it ended up as. "BuildTimes"
        // only lowers the wasm optimisation level (emscripten); IL2CPP's stripping and AOT output are unaffected.
        private static string SetCodeOptimization(string value)
        {
            try
            {
                var type = Type.GetType("UnityEditor.WebGL.UserBuildSettings, UnityEditor.WebGL.Extensions");
                var property = type?.GetProperty("codeOptimization", BindingFlags.Public | BindingFlags.Static);
                if (property == null) return "unchanged (UnityEditor.WebGL.UserBuildSettings.codeOptimization not found)";
                property.SetValue(null, Enum.Parse(property.PropertyType, value));
                return property.GetValue(null).ToString();
            }
            catch (Exception e)
            {
                return "unchanged (" + e.GetType().Name + ": " + e.Message + ")";
            }
        }

        private static string Arg(string[] args, string name)
        {
            for (var i = 0; i < args.Length - 1; i++)
                if (args[i] == name)
                    return args[i + 1];
            return null;
        }

        // A minimal JSON writer, so the editor script needs no serialization module or package.
        private static string Json(object value)
        {
            var text = new StringBuilder();
            Write(text, value);
            return text.ToString();
        }

        private static void Write(StringBuilder text, object value)
        {
            switch (value)
            {
                case null:
                    text.Append("null");
                    break;
                case string s:
                    text.Append('"');
                    foreach (var c in s)
                    {
                        if (c == '"' || c == '\\') text.Append('\\').Append(c);
                        else if (c < ' ') text.Append("\\u").Append(((int)c).ToString("x4"));
                        else text.Append(c);
                    }

                    text.Append('"');
                    break;
                case bool b:
                    text.Append(b ? "true" : "false");
                    break;
                case int _:
                case long _:
                    text.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
                    break;
                case double d:
                    text.Append(d.ToString("0.###", CultureInfo.InvariantCulture));
                    break;
                case Dictionary<string, object> map:
                    text.Append('{');
                    var first = true;
                    foreach (var pair in map)
                    {
                        if (!first) text.Append(',');
                        first = false;
                        Write(text, pair.Key);
                        text.Append(':');
                        Write(text, pair.Value);
                    }

                    text.Append('}');
                    break;
                case List<object> list:
                    text.Append('[');
                    for (var i = 0; i < list.Count; i++)
                    {
                        if (i > 0) text.Append(',');
                        Write(text, list[i]);
                    }

                    text.Append(']');
                    break;
                default:
                    Write(text, Convert.ToString(value, CultureInfo.InvariantCulture));
                    break;
            }
        }
    }
}
