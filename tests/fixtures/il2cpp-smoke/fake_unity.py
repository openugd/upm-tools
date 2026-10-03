#!/usr/bin/env python3
"""A stand-in for the Unity executable, for tests/test_il2cpp_smoke.py: it understands the arguments il2cpp-smoke.sh
passes and behaves as FAKE_UNITY_MODE says, writing what the real editor and SmokeBuild.Run would write.

  success      the log, a result file saying Succeeded, and a build folder whose index.html logs check lines and
               sets the title "OPENUGD-IL2CPP: PASS 2/2"
  failpage     as success, but the page reports "OPENUGD-IL2CPP: FAIL 1/2 lifetime-nesting"
  licence      the log line Unity writes without a valid licence, exit 1
  compile      a compile error in the log and the batchmode abort, exit 1
"""
import json
import os
import sys

args = sys.argv[1:]


def arg(name):
    return args[args.index(name) + 1] if name in args else None


mode = os.environ.get('FAKE_UNITY_MODE', 'success')
log = arg('-logFile')
with open(log, 'w') as f:
    f.write('[Licensing::Client] Error: Code 500 while processing request (harmless on successful runs)\n')
    if mode == 'licence':
        f.write('No valid Unity Editor license found. Please activate your license.\n')
        sys.exit(1)
    if mode == 'compile':
        f.write('Assets/Il2CppSmoke/Boot.cs(10,5): error CS0246: The type or namespace name \'Nope\' could not be '
                'found (are you missing a using directive or an assembly reference?)\n')
        f.write('Aborting batchmode due to failure:\nScripts have compiler errors.\n')
        sys.exit(1)
    out = arg('-smokeOutput')
    os.makedirs(os.path.join(out, 'Build'), exist_ok=True)
    if mode == 'failpage':
        lines = ['OPENUGD-IL2CPP check player-il2cpp: PASS',
                 'OPENUGD-IL2CPP check lifetime-nesting: FAIL termination order was inner,outer-2,outer-1']
        summary = 'OPENUGD-IL2CPP: FAIL 1/2 lifetime-nesting'
    else:
        lines = ['OPENUGD-IL2CPP check player-il2cpp: PASS', 'OPENUGD-IL2CPP check lifetime-nesting: PASS']
        summary = 'OPENUGD-IL2CPP: PASS 2/2'
    script = ''.join('console.log(%s);' % json.dumps(l) for l in lines + [summary])
    with open(os.path.join(out, 'index.html'), 'w') as f:
        f.write('<!doctype html><html><head><title>Unity WebGL Player</title></head><body><script src="Build/WebGL'
                '.loader.js"></script><script>setTimeout(function(){%sdocument.title=%s;},300);</script></body>'
                '</html>' % (script, json.dumps(summary)))
    for name, data in (('WebGL.loader.js', b'// loader'), ('WebGL.framework.js', b'// framework'),
                       ('WebGL.data', b'UnityWebData1.0\0'), ('WebGL.wasm', b'\0asm\1\0\0\0')):
        with open(os.path.join(out, 'Build', name), 'wb') as f:
            f.write(data)
    result = {'result': 'Succeeded', 'totalSeconds': 1.5, 'totalSize': 1234, 'errors': [], 'exit': 0,
              'settings': {'scriptingBackend': 'IL2CPP', 'managedStrippingLevel': arg('-smokeStripping')}}
    with open(arg('-smokeResult'), 'w') as f:
        json.dump(result, f)
    f = open(log, 'a')
    f.write('SmokeBuild: exit 0\n')
    f.close()
