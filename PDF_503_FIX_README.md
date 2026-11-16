# LAS CAM PDF 503 fix — 2026-11-05

## Confirmed production failure

The supplied terminal log shows:

- DPR run: `da09f704-8446-4a9b-9382-8f794c5e5863`
- Report artifact version: `3`
- `dpr_cam_pdf_render_failed ... ModuleNotFoundError("No module named 'jinja2'")`
- Completed analysis and report JSON 200; report PDF request 503.

Cause: The **active Python environment** used by the spawned PDF renderer does not have `jinja2`. The project already declares both Jinja2 and WeasyPrint as dependencies, but the deployed runtime is missing at least Jinja2. Install packages in the same virtual environment running uvicorn and the worker.

## Code changes

1. `app/services/dpr_agentic/report_store.py`: proactive PDF dependency diagnostics, validation of returned PDF header, selected-version-first PDF lookup (prevents serving an older PDF when latest PDF failed), and safe no-Gemini recovery handling.
2. `app/main.py`: report PDF dependency check at application startup, with log showing missing packages and the actual Python interpreter.
3. `scripts/rerender_cam_pdfs.py`: helpful early failure for missing renderer dependencies.
4. `tests/test_dpr_pdf_download_regression_standalone.py`: five offline regression tests, no database needed.
5. `scripts/smoke_test_cam_renderer.py`: local subprocess-based end-to-end PDF smoke test, no database or Gemini.

No changes to vendor/Wallet permissions, Gemini analysis, report JSON, customer identity, migrations, existing PDF templates, fonts, or frontend. Existing JSON report remains immutable. Recovery uses the existing migration 009 `FAILED -> RENDERED` controlled transition.

## Fix current running machine (from LAS project root)

Activate **the same environment used to start uvicorn** and run:

```bash
python -c 'import sys; print(sys.executable)'
python -m pip install 'jinja2>=3.1,<4' 'weasyprint>=70,<71'
python -c 'import jinja2, weasyprint; print(jinja2.__version__, weasyprint.__version__)'
python scripts/smoke_test_cam_renderer.py
python scripts/rerender_cam_pdfs.py --run-id da09f704-8446-4a9b-9382-8f794c5e5863
```

If using a different Python binary to start uvicorn, run `THAT_PYTHON -m pip install ...` rather than a different `pip`. The dependency check logs the interpreter used at startup.

Do not rerun Gemini. The script regenerates only failed PDF bytes from stored report JSON. If the script reports another render failure after dependencies are installed, inspect `dpr_cam_pdf_render_failed` and WeasyPrint's OS library requirements.

Check persistent state using:

```sql
SELECT run_id, source_reference, version, render_status, pdf_render_status,
       octet_length(report_pdf) AS pdf_bytes
FROM dpr_report_artifacts
WHERE run_id = 'da09f704-8446-4a9b-9382-8f794c5e5863'::uuid
ORDER BY version DESC;
```

The target version should have `pdf_render_status='RENDERED'` and nonzero `pdf_bytes` after successful repair. If deployment uses multiple containers/hosts, ensure rendering dependencies are present on **worker** instances and the API points to the same DPR PostgreSQL database.

## Tests performed in offline container

- `python -m compileall` for modified Python: passed.
- `python tests/test_dpr_pdf_download_regression_standalone.py`: 5 tests passed.
- `python scripts/smoke_test_cam_renderer.py`: passed, produced valid PDF bytes from the spawned process (without Gemini).
- Full pytest was not possible in this container: `respx` is not installed and a live test PostgreSQL server was not provided.
- No live production DB migration, recovery, or download test has been performed.

## Scope

The terminal also contains `403` responses **before** the report was complete. Those are separate pre-release access-control responses, not the later PDF 503. The `77,295` tokens shown in the log refer to the separate Gemini run and are not consumed by the PDF recovery script.
