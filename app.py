"""
app.py
------
Application entry point for CertiFlow.

Run with:  python app.py

Responsibilities are intentionally tiny here - just wire everything together:
    1. Ensure the on-disk folder structure exists.
    2. Launch the CustomTkinter UI (which owns the business/persistence layers).

Keeping the bootstrap thin makes the app easy to embed, test or wrap with a
future CLI / packaging step (PyInstaller, etc.).
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime


def _crash_log_path():
    """Where to record a startup failure.

    Deliberately does not import ``settings``: this has to work even when the
    failure *is* an import error, so the location is derived from the running
    executable instead.
    """
    from pathlib import Path

    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent
    return root / "logs" / "crash.log"


def _report_fatal(exc: BaseException) -> None:
    """Record an unrecoverable startup error and tell the user about it.

    A windowed build has no console, so an exception here would otherwise kill
    the process with no visible explanation at all. Write the traceback next to
    the executable and surface the summary in a dialog.
    """
    detail = "".join(traceback.format_exception(type(exc), exc,
                                                exc.__traceback__))
    log_path = None
    try:
        log_path = _crash_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}\n"
                     f"{'=' * 70}\n{detail}")
    except Exception:
        pass

    print(detail, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        where = f"\n\nDetails were written to:\n{log_path}" if log_path else ""
        messagebox.showerror(
            "CertiFlow could not start",
            f"{type(exc).__name__}: {exc}{where}")
        root.destroy()
    except Exception:
        pass


def selftest() -> int:
    """Verify a packaged build can actually produce documents.

    Launching the window only proves Tk loaded. This exercises the parts most
    likely to break once frozen - the bundled TrueType fonts, the branding
    images, every document renderer and the pdfium preview - by generating one
    of each document type into a temporary folder.

    Usable on any build:  ``CertiFlow.exe --selftest``
    """
    import json
    import shutil
    import tempfile
    from pathlib import Path

    import settings

    settings.ensure_directories()
    lines = []

    def say(text: str) -> None:
        lines.append(text)
        print(text)

    say(f"CertiFlow selftest  {datetime.now():%Y-%m-%d %H:%M:%S}")
    say(f"  frozen        : {settings.FROZEN}")
    say(f"  base dir      : {settings.BASE_DIR}")
    say(f"  resource dir  : {settings.RESOURCE_DIR}")

    ok = True
    workdir = Path(tempfile.mkdtemp(prefix="certiflow_selftest_"))
    try:
        import doc_router
        import pdf_generator as pg
        import pdf_preview as pv

        # -- fonts ------------------------------------------------------- #
        fonts = sorted(p.name for p in settings.FONTS_DIR.glob("*.ttf"))
        say(f"  fonts on disk : {len(fonts)} {fonts}")
        if not fonts:
            say("  FAIL: no TrueType fonts were seeded")
            ok = False
        pg.register_fonts(settings.FONTS_DIR)
        pg.register_display_fonts(settings.FONTS_DIR)
        say("  fonts         : registered")

        # -- branding ---------------------------------------------------- #
        company = settings.CompanyProfile().data
        for kind in ("logo", "signature", "stamp", "watermark"):
            value = str(company.get(kind, "") or "")
            resolved = settings.resolve_path(value)
            state = "ok" if value and resolved.exists() else "absent"
            say(f"  asset {kind:<9}: {state} {value}")

        # -- one document of every type ---------------------------------- #
        data = {
            "candidate_name": "Selftest Candidate",
            "position": "AI Agent Developer Intern",
            "role_base": "AI Agent Developer",
            "intern_id": "SO269999",
            "cert_no": "SO-INT-269999",
            "start_date": "01-07-2026",
            "end_date": "30-09-2026",
            "issue_date": datetime.now().strftime("%d-%m-%Y"),
            "department": "Engineering",
            "grade": "A",
            "remarks": "Selftest",
        }
        generator = pg.PDFGenerator(settings.BASE_DIR)

        for doc_type in doc_router.all_doc_types():
            target = workdir / f"{doc_type}.pdf"
            try:
                doc_router.render(target, company, dict(data), doc_type,
                                  settings.BASE_DIR, generator)
                size = target.stat().st_size
                if size < 4096:
                    say(f"  FAIL {doc_type}: only {size} bytes")
                    ok = False
                else:
                    say(f"  render        : {doc_type} -> "
                        f"{size // 1024} KB")
            except Exception as exc:
                say(f"  FAIL {doc_type}: {type(exc).__name__}: {exc}")
                ok = False

        # -- pdfium preview ---------------------------------------------- #
        first = next(iter(sorted(workdir.glob("*.pdf"))), None)
        if first is None:
            say("  FAIL: nothing rendered, cannot test the preview")
            ok = False
        else:
            image = pv.render_page_fit(first, 800, 600)
            if image is None:
                say("  preview       : UNAVAILABLE (pypdfium2 did not load) - "
                    "PDFs still generate, live preview and PNG/JPG export "
                    "are disabled")
                ok = False
            else:
                say(f"  preview       : ok {image.size[0]}x{image.size[1]} px")

        # -- database ---------------------------------------------------- #
        try:
            import database

            db = database.InternDatabase(settings.COMPANY_DIR / "interns.db")
            say(f"  database      : ok, {db.count()} record(s), next id "
                f"{db.peek_next_intern_id()}")
            db.close()
        except Exception as exc:
            say(f"  FAIL database : {type(exc).__name__}: {exc}")
            ok = False

        # -- full backup / restore round-trip ---------------------------- #
        # Restores into a temp folder, never over the real data.
        try:
            import data_transfer

            probe = workdir / "backup.zip"
            made = data_transfer.create_backup(probe, include_output=False)
            checked = data_transfer.inspect(probe, verify=True)
            say(f"  backup        : {made.file_count} file(s), "
                f"{made.size_bytes // 1024} KB, "
                f"{made.interns} intern(s), {made.certificates} cert(s)")
            if not checked.integrity_ok:
                say(f"  FAIL backup integrity: {checked.problems}")
                ok = False
            else:
                say("  backup verify : checksums ok")
            say(f"  backup folder : {data_transfer.backup_root()}")
        except Exception as exc:
            say(f"  FAIL backup   : {type(exc).__name__}: {exc}")
            ok = False

        # -- import / export round-trip ---------------------------------- #
        # Exercised against a throwaway database so the real records are only
        # ever read. Catches a missing openpyxl in a packaged build.
        try:
            import certificate_generator as cgen
            import intern_io

            sandbox = workdir / "io"
            sandbox.mkdir(parents=True, exist_ok=True)
            probe_db = database.InternDatabase(sandbox / "probe.db")
            probe_certs = cgen.CertificateStore(sandbox / "probe.db")
            for suffix in (".xlsx", ".json", ".csv"):
                sample = intern_io.write_sample(sandbox / f"probe{suffix}")
                archive = intern_io.read_archive(sample)
                applied = intern_io.apply_archive(probe_db, probe_certs,
                                                  archive, overwrite=True)
                if not archive.interns or applied.failed:
                    say(f"  FAIL {suffix} round-trip: "
                        f"{len(archive.interns)} intern(s), "
                        f"{applied.failed}")
                    ok = False
                else:
                    say(f"  import/export : {suffix} ok "
                        f"({len(archive.interns)} intern, "
                        f"{len(archive.certificates)} cert)")
            probe_db.close()
            probe_certs.close()
        except Exception as exc:
            say(f"  FAIL import/export : {type(exc).__name__}: {exc}")
            ok = False

        say("")
        say("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    except Exception as exc:
        say(f"SELFTEST CRASHED: {type(exc).__name__}: {exc}")
        say("".join(traceback.format_exception(type(exc), exc,
                                              exc.__traceback__)))
        ok = False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        try:
            report = settings.LOGS_DIR / "selftest.log"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"report written to {report}")
        except OSError:
            pass

    return 0 if ok else 1


def main() -> int:
    """Bootstrap and run the desktop application."""
    if "--selftest" in sys.argv[1:]:
        return selftest()

    from settings import ensure_directories

    ensure_directories()

    try:
        from ui import OfferLetterApp
    except ImportError as exc:
        # Friendly message when a dependency is missing.
        print("Missing dependency:", exc)
        print("Install requirements with:  pip install -r requirements.txt")
        raise

    app = OfferLetterApp()
    app.mainloop()
    return 0


def run() -> int:
    """``main`` with a last-resort handler, so nothing ever fails silently."""
    try:
        return main()
    except BaseException as exc:            # noqa: BLE001 - truly last resort
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _report_fatal(exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
