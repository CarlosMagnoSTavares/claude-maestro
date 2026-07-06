"""Lançador do Claude Maestro (executável instalado via PyInstaller).

Sem console (--windowed): sys.stdout/sys.stderr são None — redirecionamos para
um arquivo de log ANTES de importar qualquer coisa que escreva na saída
(uvicorn quebra com stdout None: 'NoneType' object has no attribute 'isatty').
Aceita os mesmos argumentos do `python -m maestro` (serve | run-task <id>).
"""
import os
import sys
import traceback


def _log_path() -> str:
    base = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        "ClaudeMaestro", "data", "logs")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "maestro.log")


def _fix_std_streams():
    """Em modo --windowed, stdout/stderr são None: aponta ambos para o log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = open(_log_path(), "a", encoding="utf-8", errors="replace", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def main():
    _fix_std_streams()
    try:
        from maestro.__main__ import main as maestro_main
        maestro_main()
    except SystemExit:
        raise
    except Exception:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
