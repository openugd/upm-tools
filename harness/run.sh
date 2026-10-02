#!/bin/zsh
# Level-1 gate: build every asmdef as Unity would (declared references only), editor + player, then run the
# engine-free tests. Tests tagged [Category("RequiresUnity")] need the real editor and are skipped here.
H=${0:A:h}; OUT=${OPENUGD_HARNESS_OUT:-$H/out}
python3 $H/gen.py --out $OUT "$@" || exit 2
cd $OUT; fail=0
for v in editor player; do for d in com.openugd.*.$v(N); do
  n=${d%.$v}; [[ $n == *.tests ]] && continue
  dotnet build $d/p.csproj -v q --nologo > $d/build.log 2>&1
  e=$(grep -E ": error CS" $d/build.log | grep -oE "[A-Za-z0-9/._ -]+\.cs\([0-9]+,[0-9]+\): error CS[0-9]+" | sort -u | wc -l | tr -d ' ')
  w=$(grep -E ": warning CS" $d/build.log | grep -oE "[A-Za-z0-9/._ -]+\.cs\([0-9]+,[0-9]+\): warning CS[0-9]+" | sort -u | wc -l | tr -d ' ')
  printf "build %-7s %-36s errors=%-3s warnings=%s\n" $v $n $e $w; [[ $e != 0 ]] && fail=1
done; done
for d in com.openugd.*.tests.editor(N); do
  r=$(dotnet test $d/p.csproj --nologo -v q --filter "TestCategory!=RequiresUnity" 2>&1 | grep -E "^(Passed!|Failed!)|: error CS" | sed 's| - .*||' | head -3 | tr '\n' ' ')
  printf "test  %-42s %s\n" ${d%.editor} "$r"; [[ $r == *Failed* || $r == *error* ]] && fail=1
done
exit $fail
