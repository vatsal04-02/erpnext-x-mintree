. ~/.devenv
cd ~/frappe-bench
mkdir -p ~/baseline
: > ~/baseline/summary.tsv
for m in $(ls apps/factory_ops/factory_ops/tests/test_*.py | xargs -n1 basename | sed 's/\.py$//'); do
  start=$(date +%s)
  bench --site test.localhost run-tests --module factory_ops.tests.$m > ~/baseline/$m.log 2>&1
  rc=$?
  echo -e "$m\t$rc\t$(( $(date +%s)-start ))s" >> ~/baseline/summary.tsv
done
echo ALL_MODULES_DONE >> ~/baseline/summary.tsv
