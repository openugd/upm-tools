// usage: AsmRefs <assembly.dll> [...]
// Writes one JSON object keyed by each path given:
//   {"<path>": {"name": "<assembly name>",
//               "references": {"<referenced assembly>": ["<type used from it>", ...], ...}}}
// "references" is the AssemblyRef table, i.e. exactly the assemblies the compiler recorded as needed at run time.
// The types are the TypeRef rows that resolve to each one (nested types as Outer/Inner), so a report can say
// which use pulled the assembly in. A file that cannot be read gets {"error": "..."} instead; the exit status
// is 0 unless the arguments are missing.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text.Json;

static class AsmRefs
{
    static string FullName(MetadataReader r, TypeReference tr)
    {
        var name = r.GetString(tr.Name);
        if (tr.ResolutionScope.Kind == HandleKind.TypeReference)
            return FullName(r, r.GetTypeReference((TypeReferenceHandle)tr.ResolutionScope)) + "/" + name;
        var ns = r.GetString(tr.Namespace);
        return ns.Length == 0 ? name : ns + "." + name;
    }

    // The AssemblyRef a TypeRef resolves to: nested TypeRefs are scoped by their declaring TypeRef.
    static EntityHandle Scope(MetadataReader r, TypeReference tr)
    {
        var scope = tr.ResolutionScope;
        while (scope.Kind == HandleKind.TypeReference)
            scope = r.GetTypeReference((TypeReferenceHandle)scope).ResolutionScope;
        return scope;
    }

    static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.Error.WriteLine("usage: AsmRefs <assembly.dll> [...]");
            return 2;
        }
        using var stdout = Console.OpenStandardOutput();
        using var w = new Utf8JsonWriter(stdout);
        w.WriteStartObject();
        foreach (var path in args)
        {
            w.WritePropertyName(path);
            w.WriteStartObject();
            try
            {
                using var stream = File.OpenRead(path);
                using var pe = new PEReader(stream);
                if (!pe.HasMetadata)
                    throw new BadImageFormatException("no CLI metadata");
                var r = pe.GetMetadataReader();
                var refs = new SortedDictionary<string, SortedSet<string>>(StringComparer.Ordinal);
                var byHandle = new Dictionary<AssemblyReferenceHandle, string>();
                foreach (var h in r.AssemblyReferences)
                {
                    var name = r.GetString(r.GetAssemblyReference(h).Name);
                    byHandle[h] = name;
                    if (!refs.ContainsKey(name))
                        refs[name] = new SortedSet<string>(StringComparer.Ordinal);
                }
                foreach (var h in r.TypeReferences)
                {
                    var tr = r.GetTypeReference(h);
                    var scope = Scope(r, tr);
                    if (scope.Kind == HandleKind.AssemblyReference)
                        refs[byHandle[(AssemblyReferenceHandle)scope]].Add(FullName(r, tr));
                }
                w.WriteString("name", r.IsAssembly ? r.GetString(r.GetAssemblyDefinition().Name)
                                                   : Path.GetFileNameWithoutExtension(path));
                w.WritePropertyName("references");
                w.WriteStartObject();
                foreach (var kv in refs)
                {
                    w.WritePropertyName(kv.Key);
                    w.WriteStartArray();
                    foreach (var t in kv.Value)
                        w.WriteStringValue(t);
                    w.WriteEndArray();
                }
                w.WriteEndObject();
            }
            catch (Exception e) when (e is IOException || e is BadImageFormatException ||
                                      e is UnauthorizedAccessException || e is InvalidOperationException)
            {
                w.WriteString("error", e.GetType().Name + ": " + e.Message);
            }
            w.WriteEndObject();
        }
        w.WriteEndObject();
        w.Flush();
        stdout.WriteByte((byte)'\n');
        return 0;
    }
}
