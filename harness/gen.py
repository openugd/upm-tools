#!/usr/bin/env python3
"""Generate one .csproj per asmdef, with ONLY the references the asmdef declares - the way Unity compiles.

Runtime asmdefs get two variants: 'editor' (UNITY_EDITOR + UnityEditor refs) and 'player' (neither).
Usage: gen.py [--root DIR] [--out DIR] [--unity EDITOR_DIR] [--packages a,b,c]
"""
import argparse, glob, json, os, sys

ap = argparse.ArgumentParser()
ap.add_argument('--root', default=os.environ.get('OPENUGD_ROOT', '~/workspace/openugd/v2/wt'))
ap.add_argument('--out', default=os.environ.get('OPENUGD_HARNESS_OUT', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')))
ap.add_argument('--unity', default=os.environ.get('UNITY_EDITOR', '/Applications/Unity/Hub/Editor/6000.0.41f1'))
ap.add_argument('--script-assemblies', default=os.environ.get('UNITY_SCRIPT_ASSEMBLIES', '~/workspace/openugd/OpenUGD/Library/ScriptAssemblies'))
ap.add_argument('--packages', default='upm-lifetime,upm-signal,upm-context,upm-corelib,upm-corelib-widgets,upm-ui')
a = ap.parse_args()

U = os.path.join(a.unity, 'Unity.app/Contents/Managed/UnityEngine')
SA = a.script_assemblies
OUT = os.path.abspath(a.out)
os.makedirs(OUT, exist_ok=True)

asm = {}
for p in a.packages.split(','):
    for f in glob.glob(f'{a.root}/{p}/**/*.asmdef', recursive=True):
        if '~' in f:
            continue
        d = json.load(open(f, encoding='utf-8-sig'))
        asm[d['name']] = dict(path=f, dir=os.path.dirname(f), d=d, pkg=p)
dirs = {x['dir'] for x in asm.values()}

def sources(adir):
    out = []
    for root, sub, files in os.walk(adir):
        sub[:] = [s for s in sub if not s.endswith('~') and os.path.join(root, s) not in dirs]
        out += [os.path.join(root, f) for f in files if f.endswith('.cs')]
    return out

engine_rt = glob.glob(f'{U}/UnityEngine*.dll')
engine_ed = glob.glob(f'{U}/UnityEditor*.dll')

def is_test(d): return 'TestRunner' in ' '.join(d.get('references', []))

def project(name, variant, priv):
    x = asm[name]; d = x['d']
    test = is_test(d)
    editor = variant == 'editor' or 'Editor' in d.get('includePlatforms', [])
    ref = lambda path: f'<Reference Include="{os.path.basename(path)[:-4]}"><HintPath>{path}</HintPath><Private>{priv}</Private></Reference>'
    items, prefs = [], []
    for r in d.get('references', []):
        r = r.replace('GUID:', '')
        if r in asm:
            prefs.append(f'<ProjectReference Include="{OUT}/{r}.{variant}/p.csproj" />')
        elif r == 'Unity.TextMeshPro':
            # Unity 6: TMP lives inside com.unity.ugui 2.x; the assembly name is unchanged.
            items.append(ref(f'{SA}/Unity.TextMeshPro.dll'))
        elif r in ('UnityEngine.TestRunner', 'UnityEditor.TestRunner'):
            items.append(ref(f'{SA}/{r}.dll'))
        else:
            print(f'  ! {name}: unresolved reference {r}', file=sys.stderr)
    if not d.get('noEngineReferences'):
        items += [ref(f) for f in engine_rt]
        items.append(ref(f'{SA}/UnityEngine.UI.dll'))   # Unity references ugui implicitly
        if editor:
            items += [ref(f) for f in engine_ed]
    defines = 'UNITY_6000_0_OR_NEWER;UNITY_ASSERTIONS' + (';UNITY_EDITOR;DEBUG' if editor else '')
    comp = '\n'.join(f'<Compile Include="{s}" />' for s in sources(x['dir']))
    tests = ('<PackageReference Include="NUnit" Version="3.14.0" /><PackageReference Include="NUnit3TestAdapter" Version="4.5.0" />'
             '<PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.1" />') if test else ''
    doc = '' if test else '<GenerateDocumentationFile>true</GenerateDocumentationFile>'
    return f'''<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>
<TargetFramework>{'net10.0' if test else 'netstandard2.1'}</TargetFramework><LangVersion>9</LangVersion><Nullable>disable</Nullable>
<EnableDefaultCompileItems>false</EnableDefaultCompileItems><AssemblyName>{name}</AssemblyName>
<DefineConstants>{defines}</DefineConstants><AllowUnsafeBlocks>{str(d.get("allowUnsafeCode", False)).lower()}</AllowUnsafeBlocks>
<IsPackable>false</IsPackable><DisableTransitiveProjectReferences>true</DisableTransitiveProjectReferences>{doc}<NoWarn>CS0649</NoWarn>
</PropertyGroup><ItemGroup>{comp}</ItemGroup><ItemGroup>{"".join(items)}{"".join(prefs)}{tests}</ItemGroup></Project>'''

for variant in ('editor', 'player'):
    for name, x in asm.items():
        d = x['d']
        if variant == 'player' and ('Editor' in d.get('includePlatforms', []) or is_test(d)):
            continue
        os.makedirs(f'{OUT}/{name}.{variant}', exist_ok=True)
        open(f'{OUT}/{name}.{variant}/p.csproj', 'w').write(project(name, variant, 'true' if is_test(d) else 'false'))
print(f'{len(asm)} asmdefs -> {OUT}')
