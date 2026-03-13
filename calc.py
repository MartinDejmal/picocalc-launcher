# apps/calc.py  — PicoCalc MicroPython TUI kalkulačka
# Autor: ChatGPT (pro Martyho)
# Funkce: bezpečné vyhodnocení výrazů, DEG/RAD režim, paměť, historie

import math

import sys

HISTORY_FILE = "calc_history.txt"
MAX_HISTORY = 100

# --- Stav aplikace ---
state = {
    "deg_mode": True,    # True = stupně, False = radiány
    "memory": 0.0,
    "history": []
}

# --- Fallbacky pro MicroPython ---
TAU = getattr(math, "tau", 2 * math.pi)

def _iter_factorial(n):
    n = int(n)
    if n < 0:
        raise ValueError("factorial() not defined for negatives")
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r

FACTORIAL = getattr(math, "factorial", _iter_factorial)

def build_namespace(deg_mode, memory_val):
    # trig zabalené dle režimu
    if deg_mode:
        sin = lambda x: math.sin(math.radians(x))
        cos = lambda x: math.cos(math.radians(x))
        tan = lambda x: math.tan(math.radians(x))
        asin = lambda x: math.degrees(math.asin(x))
        acos = lambda x: math.degrees(math.acos(x))
        atan = lambda x: math.degrees(math.atan(x))
    else:
        sin, cos, tan = math.sin, math.cos, math.tan
        asin, acos, atan = math.asin, math.acos, math.atan

    ns = {
        # trig
        "sin": sin, "cos": cos, "tan": tan,
        "asin": asin, "acos": acos, "atan": atan,
        # běžné
        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "exp": math.exp, "pow": pow, "abs": abs,
        "floor": math.floor, "ceil": math.ceil, "factorial": FACTORIAL,
        # konstanty
        "pi": math.pi, "e": math.e, "tau": TAU,
        # paměť
        "M": memory_val,
    }
    return ns




# --- Minimal parser bez 'ast' (MicroPython-friendly) -------------------
class _Lexer:
    def __init__(self, s):
        self.s = s
        self.i = 0
        self.n = len(s)
        self.tok = None
        self.val = None
        self._next()

    def _peek(self, k=0):
        j = self.i + k
        return self.s[j] if j < self.n else ''

    def _isid0(self, ch):
        return ch.isalpha() or ch == '_'

    def _isid(self, ch):
        return ch.isalnum() or ch == '_'

    def _number(self):
        start = self.i
        has_dot = False
        has_exp = False
        while self.i < self.n:
            ch = self.s[self.i]
            if ch.isdigit():
                self.i += 1
            elif ch == '.' and not has_dot and not has_exp:
                has_dot = True; self.i += 1
            elif (ch in 'eE') and not has_exp:
                has_exp = True; self.i += 1
                if self._peek() in '+-':
                    self.i += 1
            else:
                break
        txt = self.s[start:self.i]
        try:
            if '.' in txt or 'e' in txt or 'E' in txt:
                return float(txt)
            return int(txt)
        except:
            raise ValueError("Invalid number: %r" % txt)

    def _ident(self):
        start = self.i
        self.i += 1
        while self.i < self.n and self._isid(self.s[self.i]):
            self.i += 1
        return self.s[start:self.i]

    def _next(self):
        s, n = self.s, self.n
        while self.i < n and s[self.i].isspace():
            self.i += 1
        if self.i >= n:
            self.tok, self.val = 'EOF', None
            return
        ch = s[self.i]

        # dvoznakové operátory
        if ch == '*' and self._peek(1) == '*':
            self.i += 2; self.tok, self.val = 'OP', '**'; return
        if ch == '/' and self._peek(1) == '/':
            self.i += 2; self.tok, self.val = 'OP', '//'; return

        # jednoznakové
        if ch in '+-*/%(),':
            self.i += 1
            if ch in '+-*/%':
                self.tok, self.val = 'OP', ch
            elif ch == '(':
                self.tok, self.val = 'LP', ch
            elif ch == ')':
                self.tok, self.val = 'RP', ch
            else:
                self.tok, self.val = 'COMMA', ch
            return

        # číslo / identifikátor
        if ch.isdigit() or (ch == '.' and self._peek(1).isdigit()):
            self.val = self._number(); self.tok = 'NUM'; return
        if self._isid0(ch):
            self.val = self._ident(); self.tok = 'ID'; return

        raise ValueError("Unknown character: %r" % ch)

class _Parser:
    def __init__(self, s, ns):
        self.lx = _Lexer(s)
        self.ns = ns

    def parse(self):
        v = self.expr()
        self._expect('EOF')
        return v

    # expr := term { (+|-) term }*
    def expr(self):
        v = self.term()
        while self._is('OP', '+') or self._is('OP', '-'):
            op = self.lx.val; self.lx._next()
            rhs = self.term()
            v = v + rhs if op == '+' else v - rhs
        return v

    # term := power { (*|/|//|%) power }*
    def term(self):
        v = self.power()
        while self._is('OP') and self.lx.val in ('*', '/', '//', '%'):
            op = self.lx.val; self.lx._next()
            rhs = self.power()
            if op == '*':   v = v * rhs
            elif op == '/': v = v / rhs
            elif op == '//': v = v // rhs
            else:           v = v % rhs
        return v

    # power := unary { ** unary }*   (pravá asociativita)
    def power(self):
        v = self.unary()
        while self._is('OP', '**'):
            self.lx._next()
            rhs = self.unary()
            v = v ** rhs
        return v

    # unary := (+|-) unary | primary
    def unary(self):
        if self._is('OP', '+'):
            self.lx._next()
            return +self.unary()
        if self._is('OP', '-'):
            self.lx._next()
            return -self.unary()
        return self.primary()

    # primary := NUM | ID ( '(' args? ')' )? | '(' expr ')'
    def primary(self):
        if self._is('NUM'):
            v = self.lx.val; self.lx._next(); return v
        if self._is('ID'):
            name = self.lx.val; self.lx._next()
            # konstanta nebo volání funkce
            if self._is('LP'):
                self.lx._next()
                args = []
                if not self._is('RP'):
                    args.append(self.expr())
                    while self._is('COMMA'):
                        self.lx._next()
                        args.append(self.expr())
                self._expect('RP'); self.lx._next()
                func = self._lookup(name)
                if not callable(func):
                    raise ValueError("Není funkce: %s" % name)
                return func(*args)
            # prosté jméno (konstanta)
            return self._lookup(name)
        if self._is('LP'):
            self.lx._next()
            v = self.expr()
            self._expect('RP'); self.lx._next()
            return v
        raise ValueError("Expected: number, name or parentheses.")

    def _lookup(self, name):
        if name in self.ns:
            return self.ns[name]
        raise ValueError("Unknown name: %s" % name)

    def _is(self, t, v=None):
        if self.lx.tok != t: return False
        return True if v is None else (self.lx.val == v)

    def _expect(self, t):
        if self.lx.tok != t:
            raise ValueError("Expected: %s" % t)


# --- Nástroje: načtení/uložení historie ---
def load_history():
    try:
        with open(HISTORY_FILE, "r") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            state["history"] = lines[-MAX_HISTORY:]
    except Exception:
        state["history"] = []

def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            for item in state["history"][-MAX_HISTORY:]:
                f.write(item + "\n")
    except Exception:
        pass



# --- Matematické jméno → funkce/konstanta ---
def build_namespace(deg_mode, memory_val):
    # trig zabalené dle režimu
    if deg_mode:
        sin = lambda x: math.sin(math.radians(x))
        cos = lambda x: math.cos(math.radians(x))
        tan = lambda x: math.tan(math.radians(x))
        asin = lambda x: math.degrees(math.asin(x))
        acos = lambda x: math.degrees(math.acos(x))
        atan = lambda x: math.degrees(math.atan(x))
    else:
        sin, cos, tan = math.sin, math.cos, math.tan
        asin, acos, atan = math.asin, math.acos, math.atan

    ns = {
        # trig
        "sin": sin, "cos": cos, "tan": tan,
        "asin": asin, "acos": acos, "atan": atan,
        # běžné
        "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
        "exp": math.exp, "pow": pow, "abs": abs,
        "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
        # konstanty
        "pi": math.pi, "e": math.e, "tau": math.tau,
        # paměť
        "M": memory_val,
    }
    return ns

# --- Bezpečný evaluator: AST -> vypočet ---
def safe_eval(expr, ns):
    """
    Vyhodnocení výrazů bez 'ast'.
    Podporuje: čísla, ( ), + - * / // % **, unární +/-, jména/funkce z ns.
    """
    return _Parser(expr, ns).parse()
# ----------------------------------------------------------------------


# --- UI pomocné funkce ---
def print_header():
    mode = "DEG" if state["deg_mode"] else "RAD"
    print("\n=== PicoCalc — scientific calculator ===")
    print("Mode:", mode, "| Memory M =", state["memory"])
    print("Help: :help")

def print_help():
    print("""
Controls:
  Write expression directly, eg.:  2*(3+7)/5,  sin(30),  log10(1000)
  Commands (starting with semicolon):
    :deg      — mode switch to degrees
    :rad      — mode switch to radians
    :m= expr  — stores result into memory (M+)
    :mr       — prints memory
    :mc       — clears memory
    :hist     — shows history
    :help     — this help
    :quit     — exit

Available functions: sin, cos, tan, asin, acos, atan,
      sqrt, log, log10, exp, pow, abs, floor, ceil, factorial, pi, e, tau, M
""")

def do_command(line):
    cmd = line.strip().lower()
    if cmd == ":deg":
        state["deg_mode"] = True
        print("Switched to degrees (DEG).")
    elif cmd == ":rad":
        state["deg_mode"] = False
        print("Switched to radians (RAD).")
    elif cmd.startswith(":m="):
        expr = line[3:].strip()
        if not expr:
            print("Usage: :m= <expression>")
            return
        ns = build_namespace(state["deg_mode"], state["memory"])
        try:
            val = safe_eval(expr, ns)
            state["memory"] = float(val)
            print("M =", state["memory"])
        except Exception as e:
            print("Error M+:", e)
    elif cmd == ":mr":
        print("M =", state["memory"])
    elif cmd == ":mc":
        state["memory"] = 0.0
        print("Memory cleared.")
    elif cmd == ":hist":
        if not state["history"]:
            print("(History empty)")
        else:
            print("-- History --")
            for i, item in enumerate(state["history"][-20:], 1):
                print("{:2d}: {}".format(i, item))
    elif cmd == ":help":
        print_help()
# v do_command(), při :quit
    elif cmd == ":quit":
        print("Exiting...")
        save_history()
        raise SystemExit  # místo sys.exit()
    else:
        print("Unknown command. Try :help")

def main():
    load_history()
    print_header()
    while True:
        try:
            line = input("Calc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            save_history()
            break

        if not line:
            continue

        if line.startswith(":"):
            do_command(line)
            continue

        ns = build_namespace(state["deg_mode"], state["memory"])
        try:
            val = safe_eval(line, ns)
            # formátování výsledku
            if isinstance(val, float):
                # hezký tisk: plovoucí s ořezem zbytečných nul
                out = ("{:.12g}".format(val))
            else:
                out = str(val)
            print("=", out)

            # ulož do historie (výraz = výsledek)
            item = "{} = {}".format(line, out)
            state["history"].append(item)
            if len(state["history"]) > MAX_HISTORY:
                state["history"] = state["history"][-MAX_HISTORY:]
            save_history()

        except Exception as e:
            print("Exception:", e)

if __name__ == "__main__":
    main()
