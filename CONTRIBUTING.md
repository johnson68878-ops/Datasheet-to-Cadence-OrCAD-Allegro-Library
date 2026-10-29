# Contributing

Start with docs/ARCHITECTURE.md and docs/KNOWN_ISSUES.md. The highest-value work is generic evidence handling, table scope and geometric role binding.

1. Open a bug report with the observed behavior and expected structural behavior.
2. Provide a small synthetic example you are allowed to publish. Never attach confidential datasheets, customer CAD, license files, tokens or internal logs.
3. Add a failing regression and an intentionally conflicting example; use unrelated names and renamed copies.
4. Make the smallest general correction. Preserve raw units, bounds, page/region and conflicting sources.
5. Run `python -m pytest -q`. Report exact passed/failed/skipped counts and dependency environment.
6. For CAD results report exact application version, generated artifact, independent readback status and remaining risks. Screenshots alone are not native proof.
7. Add a development-history note, including limitations and rejected approaches.

Do not change OrCAD grouping/layout as a side effect of Allegro fixes. Do not add device answer registries or weaken source checks to produce more files. Native readback agreement is not proof of source correctness.

By submitting a contribution you agree to license your contribution under MIT and confirm you have permission to publish it. Third-party material retains its own license.
