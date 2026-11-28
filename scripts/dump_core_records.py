#!/usr/bin/env python3

"""
dump_core_records.py --- writes each JSON parity record of the core and the v0.4.0 oracle

The core area's digests differ between CI cells on canonical_bytes_on_generated_json/values and
strict_json_on_hostile_text/parsed and /nesting. This writes, for the inputs of those two cases,
one line per record: kind, index, input (the value's repr with numpy arrays printed in full, or
the text's bytes in hex), output (canonical bytes in hex, or the parse outcome and nesting) and
the output's SHA-256. It writes `<prefix>-core.tsv` from the working tree, then runs itself
under the oracle venv's interpreter for `<prefix>-oracle.tsv`, so cells can be compared line by
line. Run under PYTHONHASHSEED=0: a set's repr follows its hash order.

Usage: `uv run python scripts/dump_core_records.py <prefix>`

Contains:
    record_lines(): lists the record lines of the two JSON cases' inputs for one e8s
    write_lines(): writes record lines as one UTF-8 text file
    main(): dumps the core side, then the oracle side through the oracle's interpreter
"""

import argparse
import hashlib
import importlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
ORACLE_SIDE = "oracle"
CORE_SIDE = "core"
DOCUMENT = "doc.json"
SEED_VALUES = 400


def _outcome(call: Callable[[], str]) -> str:
    try:
        return call()
    except Exception as error:
        to_dict = getattr(error, "to_dict", None)
        detail = repr(to_dict()) if callable(to_dict) else repr(str(error))
        return f"error {type(error).__name__} {detail}"


def _one_line(text: str) -> str:
    return text.replace("\t", "\\t").replace("\n", "\\n")


def _line(kind: str, index: int, shown: str, output: str) -> str:
    digest = hashlib.sha256(output.encode("utf-8", "backslashreplace")).hexdigest()
    return f"{kind}\t{index}\t{_one_line(shown)}\t{_one_line(output)}\t{digest}"


def _canonical(e8s: ModuleType, value: object) -> str:
    return _outcome(lambda: "ok " + bytes(e8s.jcs(value)).hex())


def _parsed(strict: ModuleType, raw: bytes) -> str:
    parsed = _outcome(lambda: "ok " + repr(strict.parse_strict_json(raw, document=DOCUMENT)))
    nesting = _outcome(lambda: repr(strict.json_nesting(raw)))
    return f"{parsed} nesting={nesting}"


def record_lines(e8s: ModuleType) -> list[str]:
    """Lists the record lines of the two JSON cases' inputs for one e8s.

    Args:
        e8s: The e8s package whose outputs are dumped.

    Returns:
        lines: `canonical` lines for the generated values, `seed` lines for the values whose
            canonical bytes seed the strict texts, and `strict` lines for every hostile text.
    """
    numpy = importlib.import_module("numpy")
    numpy.set_printoptions(floatmode="unique", threshold=sys.maxsize, linewidth=sys.maxsize)
    core_json = importlib.import_module("tests.parity.core_json")
    json_inputs = importlib.import_module("tests.parity.json_inputs")
    strict = importlib.import_module(f"{e8s.__name__}.core.strict_json")
    lines: list[str] = []
    rng = e8s.seeded_rng(core_json.SEED)
    for index in range(core_json.GENERATED_VALUES):
        value = json_inputs.generated_value(rng)
        lines.append(_line("canonical", index, repr(value), _canonical(e8s, value)))
    seed_rng = e8s.seeded_rng(core_json.SEED + 2)
    for index in range(SEED_VALUES):
        value = json_inputs.generated_value(seed_rng)
        lines.append(_line("seed", index, repr(value), _canonical(e8s, value)))
    seeds = core_json._seed_texts(e8s)
    mutants = json_inputs.mutants(e8s.seeded_rng(core_json.SEED + 1), seeds, core_json.MUTANTS)
    texts = [*json_inputs.HAND_PICKED, *seeds, *mutants]
    for index, raw in enumerate(texts):
        lines.append(_line("strict", index, raw.hex(), _parsed(strict, raw)))
    return lines


def write_lines(path: Path, lines: list[str]) -> None:
    """Writes record lines as one UTF-8 text file, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{line}\n" for line in lines)
    path.write_bytes(text.encode("utf-8", "backslashreplace"))
    print(f"Wrote {path} ({len(lines)} records)", flush=True)


def _dump_oracle(prefix: Path) -> None:
    """Runs this script under the oracle venv's interpreter, from the repository root."""
    sys.path.insert(0, str(ROOT))
    build_oracle = importlib.import_module("scripts.build_oracle").build_oracle
    python = build_oracle()
    out = prefix.with_name(f"{prefix.name}-{ORACLE_SIDE}.tsv").absolute()
    command = [str(python), "-s", "-P", "-B", str(SCRIPT), str(out), "--side", ORACLE_SIDE]
    subprocess.run(command, check=True, cwd=ROOT)


def main() -> None:
    """Dumps the core side, then the oracle side through the oracle's interpreter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="file prefix, or the file itself with --side")
    parser.add_argument("--side", choices=[CORE_SIDE, ORACLE_SIDE], default=CORE_SIDE)
    args = parser.parse_args()
    if args.side == ORACLE_SIDE:
        e8s = importlib.import_module("e8s")
        if Path(str(e8s.__file__)).resolve().is_relative_to(ROOT / "e8s"):
            raise SystemExit("E: the oracle side imported the working tree's e8s")
        sys.path.append(str(ROOT))
        write_lines(args.out, record_lines(e8s))
        return
    sys.path.insert(0, str(ROOT))
    e8s = importlib.import_module("e8s")
    prefix: Path = args.out
    write_lines(prefix.with_name(f"{prefix.name}-{CORE_SIDE}.tsv"), record_lines(e8s))
    _dump_oracle(prefix)


if __name__ == "__main__":
    main()
