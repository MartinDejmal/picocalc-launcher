# launcher.py — jednoduchý souborový launcher pro PicoCalc (MicroPython)
# Prochází / a /sd (pokud existuje) a spouští .py soubory.
# Spuštění: run("/sd/apps/calc.py") nebo z menu.

import os
import sys

# --- Bezpečné spuštění souboru ---
def run(path):
    path = path.strip()
    if not path:
        print("Chybná cesta.")
        return
    try:
        with open(path, "r") as f:
            code = f.read()
    except Exception as e:
        print("Nepodařilo se načíst:", e)
        return

    # Izolované globály pro spuštěný skript (ale s přístupem k run, os, sys)
    g = {
        "__name__": "__main__",
        "run": run,
        "os": os,
        "sys": sys,
    }
    try:
        exec(code, g, None)
    except SystemExit:
        # dovolíme skriptu volat sys.exit()
        pass
    except Exception as e:
        print("Chyba při běhu:", e)

# --- Pomocné funkce pro FS ---
def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def list_dir(path):
    # Vrací seznam (name, is_dir) se seřazením: adresáře napřed, pak soubory.
    try:
        items = os.listdir(path)
    except Exception as e:
        print("Nelze číst adresář:", path, e)
        return []

    entries = []
    for name in items:
        full = path.rstrip("/") + "/" + name if path != "/" else "/" + name
        try:
            st = os.stat(full)
            is_dir = (st[0] & 0x4000) != 0 if hasattr(st, "__getitem__") else False  # FAT/MPy dir flag
        except Exception:
            is_dir = False
        entries.append((name, is_dir))
    # adresáře napřed, abecedně
    entries.sort(key=lambda x: (not x[1], x[0].lower()))
    return entries

def join(base, name):
    if base == "/":
        return "/" + name
    if base.endswith("/"):
        return base + name
    return base + "/" + name

def is_py(name):
    return name.endswith(".py")

# --- TUI smyčka ---
def browse():
    roots = ["/"]
    if exists("/sd"):
        roots.append("/sd")

    # Začneme v /sd pokud je, jinak v /
    cur = "/sd" if "/sd" in roots else "/"

    history = []

    while True:
        print("\n=== PicoCalc Launcher ===")
        print("Aktuální cesta:", cur)
        print("Příkazy: číslo=otevřít, r číslo=spustit, ..=zpět, / = kořen, q=ukončit")
        print("        spustit ručně: run(<cesta>)")
        print("------------------------")

        items = list_dir(cur)

        # Přidáme pseudo-položky kořenů (jen pokud jsme v / a existuje /sd apod.)
        # Reálně to ale řešíme tím, že se dá skočit na "/" zadáním "/".
        shown = []
        idx = 1
        for name, is_dir in items:
            full = join(cur, name)
            tag = "[D]" if is_dir else "   "
            if is_dir or is_py(name):
                print("{:2d}. {} {}".format(idx, tag, name))
                shown.append((full, is_dir))
                idx += 1

        if not shown:
            print("(Adresář je prázdný nebo neobsahuje .py soubory)")

        choice = input("Volba> ").strip()

        if choice == "q":
            print("Ukončuji launcher.")
            return
        if choice == "/":
            cur = "/"
            history = []
            continue
        if choice == "..":
            if cur == "/":
                # v kořeni už zpět nejde, pokud je /sd, nabídneme skok
                if exists("/sd"):
                    cur = "/sd"
                continue
            # o krok zpět
            parent = cur.rsplit("/", 1)[0]
            cur = parent if parent else "/"
            if history:
                history.pop()
            continue
        if choice.startswith("r "):
            # r <index> -> spustit soubor
            arg = choice[2:].strip()
            if not arg.isdigit():
                print("Použij: r <číslo položky>")
                continue
            i = int(arg)
            if i < 1 or i > len(shown):
                print("Mimo rozsah.")
                continue
            path, is_dir = shown[i-1]
            if is_dir:
                print("Tohle je adresář.")
                continue
            if not path.endswith(".py"):
                print("Spouštět lze jen .py soubory.")
                continue
            print("Spouštím:", path)
            run(path)
            continue

        # Číselná volba -> otevřít (do adresáře) nebo spustit (pokud soubor)
        if choice.isdigit():
            i = int(choice)
            if i < 1 or i > len(shown):
                print("Mimo rozsah.")
                continue
            path, is_dir = shown[i-1]
            if is_dir:
                history.append(cur)
                cur = path
            else:
                # soubor .py — rovnou se zeptáme, jestli spustit
                ans = input("Spustit '{}'? [y/N] ".format(path)).strip().lower()
                if ans == "y":
                    print("Spouštím:", path)
                    run(path)
            continue

        # Neznámá volba
        print("Neplatná volba.")

# --- Start ---
if __name__ == "__main__":
    # exportujeme run() do globálního prostoru, aby šlo volat i z REPL
    globals()["run"] = run
    try:
        browse()
    except KeyboardInterrupt:
        print("\nPřerušeno.")

