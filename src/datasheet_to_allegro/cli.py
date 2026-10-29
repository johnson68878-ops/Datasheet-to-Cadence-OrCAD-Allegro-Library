from __future__ import annotations

import argparse
import json
from pathlib import Path

from .allegro import generate_skill
from .render import write_bundle
from .library_catalog import write_reference_catalog
from .pipeline import inspect_datasheet


def main() -> None:
    parser = argparse.ArgumentParser(prog="ds2allegro")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Extract evidence, spec, table and SVG preview")
    inspect.add_argument("pdf", type=Path)
    inspect.add_argument("--output", type=Path, default=Path("output"))
    skill = sub.add_parser("generate-skill", help="Generate gated Allegro SKILL staging script")
    skill.add_argument("spec", type=Path)
    skill.add_argument("--profile", type=Path, required=True)
    skill.add_argument("--approved-by", required=True)
    skill.add_argument("--output", type=Path, default=Path("output/draft/generated_footprint.il"))
    catalog = sub.add_parser("catalog-board", help="Build a traceable candidate catalog from an Allegro library dump")
    catalog.add_argument("--board", type=Path, required=True)
    catalog.add_argument("--dump-dir", type=Path, required=True)
    catalog.add_argument("--output", type=Path, default=Path("profiles/reference_library_catalog.json"))
    args = parser.parse_args()
    if args.command == "inspect":
        fingerprint, specs = inspect_datasheet(args.pdf)
        bundles = [str(write_bundle(spec, args.output)) for spec in specs]
        summary = {
            "source": str(args.pdf),
            "fingerprint": {
                "page_count": fingerprint.page_count,
                "title_hint": fingerprint.title_hint,
                "detected_families": fingerprint.detected_families,
                "text_confidence": fingerprint.text_confidence,
            },
            "packages": bundles,
            "package_candidates_found": bool(specs),
            "skill_generation_blocked": True,
            "next_action": None if specs else "未取得足够封装证据；请审阅封装图、尺寸表及OCR结果，不注册料号专用解析器。",
        }
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "inspection_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    elif args.command == "generate-skill":
        generate_skill(args.spec, args.profile, args.approved_by, args.output)
        print(args.output)
    else:
        print(write_reference_catalog(args.board, args.dump_dir, args.output))


if __name__ == "__main__":
    main()
