# shared helpers for hooks (sourced)
root_dir(){ git rev-parse --show-toplevel 2>/dev/null || pwd; }

# On Windows "python3" (and sometimes "python") can be a Microsoft Store stub that
# only prints "Python was not found" - every post-edit check then reported a false
# syntax error. Use the first interpreter that actually runs.
py(){
  local cand
  for cand in python python3 py; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys' >/dev/null 2>&1; then
      echo "$cand"; return
    fi
  done
  for cand in "${LOCALAPPDATA:-/nonexistent}/Programs/Python/Python310/python.exe" /c/Users/*/AppData/Local/Programs/Python/Python3*/python.exe; do
    if [ -x "$cand" ] && "$cand" -c 'import sys' >/dev/null 2>&1; then
      echo "$cand"; return
    fi
  done
  echo python
}

tool_file(){ node -e '
let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s);
process.stdout.write((j.tool_input&&(j.tool_input.file_path||j.tool_input.path))||"")}catch(e){}})' 2>/dev/null \
  || "$(py)" -c 'import sys,json
try:
  j=json.load(sys.stdin); print((j.get("tool_input") or {}).get("file_path") or (j.get("tool_input") or {}).get("path") or "", end="")
except Exception: pass' 2>/dev/null; }

# C:\Users\x\file.py -> /c/Users/x/file.py (bash tools and awk choke on backslashes).
to_posix(){
  if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"
  else printf '%s' "$1" | sed -E 's#\\#/#g; s#^([A-Za-z]):#/\L\1#'
  fi
}

# The project an edited file belongs to: the nearest ancestor holding .git or app.py.
# The live app (C:\Users\th_em) is not the repository this kit sits in, so using the
# kit's own git root compared live files against the stale snapshot.
project_root_for(){
  local dir
  dir="$(dirname "$(to_posix "$1")")"
  while [ -n "$dir" ] && [ "$dir" != "/" ] && [ "$dir" != "." ]; do
    if [ -d "$dir/.git" ] || [ -f "$dir/app.py" ]; then echo "$dir"; return; fi
    dir="$(dirname "$dir")"
  done
  root_dir
}
