// usage: Inspect <assembly.dll> [...]
// Writes one JSON object per assembly: every type with its methods (name, parameter count, attributes),
// fields and properties (with accessor presence) and the attributes on each, as they exist in the file.
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Text;

static class Inspect
{
    static MetadataReader _r;

    static string TypeName(EntityHandle h)
    {
        switch (h.Kind)
        {
            case HandleKind.TypeDefinition:
                return DefName((TypeDefinitionHandle)h);
            case HandleKind.TypeReference:
                var tr = _r.GetTypeReference((TypeReferenceHandle)h);
                var ns = _r.GetString(tr.Namespace);
                var name = _r.GetString(tr.Name);
                if (tr.ResolutionScope.Kind == HandleKind.TypeReference)
                    return TypeName((EntityHandle)tr.ResolutionScope) + "/" + name;
                return ns.Length == 0 ? name : ns + "." + name;
            case HandleKind.TypeSpecification:
                return "<typespec>";
            default:
                return "<" + h.Kind + ">";
        }
    }

    static string DefName(TypeDefinitionHandle h)
    {
        var td = _r.GetTypeDefinition(h);
        var name = _r.GetString(td.Name);
        var decl = td.GetDeclaringType();
        if (!decl.IsNil) return DefName(decl) + "/" + name;
        var ns = _r.GetString(td.Namespace);
        return ns.Length == 0 ? name : ns + "." + name;
    }

    static List<string> Attrs(CustomAttributeHandleCollection handles)
    {
        var list = new List<string>();
        foreach (var h in handles)
        {
            var ca = _r.GetCustomAttribute(h);
            string type;
            if (ca.Constructor.Kind == HandleKind.MemberReference)
                type = TypeName(_r.GetMemberReference((MemberReferenceHandle)ca.Constructor).Parent);
            else if (ca.Constructor.Kind == HandleKind.MethodDefinition)
                type = DefName(_r.GetMethodDefinition((MethodDefinitionHandle)ca.Constructor).GetDeclaringType());
            else
                type = "<" + ca.Constructor.Kind + ">";
            list.Add(type);
        }
        return list;
    }

    static int ParamCount(BlobHandle sig)
    {
        var br = _r.GetBlobReader(sig);
        var header = br.ReadSignatureHeader();
        if (header.IsGeneric) br.ReadCompressedInteger();
        return br.ReadCompressedInteger();
    }

    static string J(string s)
    {
        var sb = new StringBuilder("\"");
        foreach (var c in s)
        {
            if (c == '"' || c == '\\') sb.Append('\\').Append(c);
            else if (c < 0x20) sb.AppendFormat("\\u{0:x4}", (int)c);
            else sb.Append(c);
        }
        return sb.Append('"').ToString();
    }

    static string JList(List<string> items)
    {
        var parts = new List<string>();
        foreach (var i in items) parts.Add(J(i));
        return "[" + string.Join(",", parts) + "]";
    }

    static int Main(string[] args)
    {
        var all = new List<string>();
        foreach (var path in args)
        {
            using var stream = File.OpenRead(path);
            using var pe = new PEReader(stream);
            _r = pe.GetMetadataReader();
            var types = new List<string>();
            foreach (var th in _r.TypeDefinitions)
            {
                var td = _r.GetTypeDefinition(th);
                var methods = new List<string>();
                foreach (var mh in td.GetMethods())
                {
                    var md = _r.GetMethodDefinition(mh);
                    methods.Add("{\"name\":" + J(_r.GetString(md.Name)) + ",\"params\":" + ParamCount(md.Signature) +
                                ",\"static\":" + ((md.Attributes & System.Reflection.MethodAttributes.Static) != 0 ? "true" : "false") +
                                ",\"attrs\":" + JList(Attrs(md.GetCustomAttributes())) + "}");
                }
                var fields = new List<string>();
                foreach (var fh in td.GetFields())
                {
                    var fd = _r.GetFieldDefinition(fh);
                    fields.Add("{\"name\":" + J(_r.GetString(fd.Name)) + ",\"attrs\":" + JList(Attrs(fd.GetCustomAttributes())) + "}");
                }
                var props = new List<string>();
                foreach (var ph in td.GetProperties())
                {
                    var pd = _r.GetPropertyDefinition(ph);
                    var acc = pd.GetAccessors();
                    props.Add("{\"name\":" + J(_r.GetString(pd.Name)) + ",\"getter\":" + (acc.Getter.IsNil ? "false" : "true") +
                              ",\"setter\":" + (acc.Setter.IsNil ? "false" : "true") + ",\"attrs\":" + JList(Attrs(pd.GetCustomAttributes())) + "}");
                }
                var baseType = td.BaseType.IsNil ? "" : TypeName(td.BaseType);
                types.Add(J(DefName(th)) + ":{\"base\":" + J(baseType) + ",\"attrs\":" + JList(Attrs(td.GetCustomAttributes())) +
                          ",\"methods\":[" + string.Join(",", methods) + "],\"fields\":[" + string.Join(",", fields) +
                          "],\"properties\":[" + string.Join(",", props) + "]}");
            }
            var asmName = _r.IsAssembly ? _r.GetString(_r.GetAssemblyDefinition().Name) : Path.GetFileNameWithoutExtension(path);
            all.Add(J(asmName) + ":{" + string.Join(",", types) + "}");
        }
        Console.WriteLine("{" + string.Join(",", all) + "}");
        return 0;
    }
}
